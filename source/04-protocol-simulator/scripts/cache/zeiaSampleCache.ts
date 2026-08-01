import JSZip from "jszip";
import fs from "node:fs/promises";
import path from "node:path";
import { referencedMeshGuidsFromTexts, selectArchiveMeshEntries } from "../../src/data/meshArchiveSelection";

/** Optional override: relative or absolute path to a local ZEIA used for sample caching. */
export const SAMPLE_ZEIA_ENV = "TECAN_SIMULATOR_SAMPLE_ZEIA";

export const SAMPLE_ZEIA_CACHE_RELATIVE = "source/04-protocol-simulator/.cache/zeia-samples";

export const MAX_ZEIA_CONNECTOR_ENTRIES = 4000;
export const MAX_ZEIA_FALLBACK_CONNECTOR_ENTRIES = 200;

const EMPTY_GUID = "00000000-0000-0000-0000-000000000000";
const GUID_IN_TEXT_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const CACHE_VERSION = 1;

type ArchiveEntry = JSZip.JSZipObject;

export type ZeiaSampleFile = {
  id: string;
  kind: string;
  label: string;
  path: string;
  archivePath?: string;
  entryPath?: string;
};

type CachedZeiaSampleFile = {
  id: string;
  kind: string;
  label: string;
  relativePath: string;
};

export type ZeiaSampleCacheManifest = {
  version: number;
  sourceZeiaPath: string;
  sourceFingerprint: ZeiaSourceFingerprint;
  files: CachedZeiaSampleFile[];
};

export type ZeiaSourceFingerprint = {
  size: number;
  mtimeMs: number;
};

export type ZeiaMiningProgress = {
  onPhase?: (message: string, percent: number) => void;
  onTransferScriptScan?: (current: number, total: number) => void;
};

export function hashText(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

export function resolveProjectPath(projectRoot: string, relativePath: string): string {
  const absolutePath = path.resolve(projectRoot, relativePath);
  const normalizedRoot = path.resolve(projectRoot);
  const relative = path.relative(normalizedRoot, absolutePath);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("Path outside project root");
  }
  return absolutePath;
}

export async function resolveSampleZeiaRelativePath(projectRoot: string): Promise<string | null> {
  const fromEnv = (process.env[SAMPLE_ZEIA_ENV] || "").trim();
  if (fromEnv) {
    const absolute = path.isAbsolute(fromEnv) ? path.resolve(fromEnv) : path.resolve(projectRoot, fromEnv);
    try {
      await fs.access(absolute);
    } catch {
      return null;
    }
    return toProjectRelativePath(projectRoot, absolute);
  }

  const readyRoot = path.join(projectRoot, "ready-to-import");
  const patterns = [
    path.join(readyRoot, "*", "source", "original-sources", "*.zeia"),
    path.join(readyRoot, "*", "original_sources", "*.zeia"),
    path.join(readyRoot, "*", "source", "original_sources", "*.zeia")
  ];
  // Manual walk — avoid glob dependency.
  try {
    const bundles = await fs.readdir(readyRoot, { withFileTypes: true });
    const found: string[] = [];
    for (const bundle of bundles) {
      if (!bundle.isDirectory() || bundle.name.startsWith(".")) continue;
      for (const rel of [
        ["source", "original-sources"],
        ["original_sources"],
        ["source", "original_sources"]
      ]) {
        const dir = path.join(readyRoot, bundle.name, ...rel);
        let names: string[] = [];
        try {
          names = await fs.readdir(dir);
        } catch {
          continue;
        }
        for (const name of names) {
          if (!name.toLowerCase().endsWith(".zeia")) continue;
          found.push(toProjectRelativePath(projectRoot, path.join(dir, name)));
        }
      }
    }
    found.sort();
    return found[0] || null;
  } catch {
    return null;
  }
}

export function sampleZeiaCacheRelative(sourceZeiaRelative: string): string {
  const stem = path.basename(sourceZeiaRelative, path.extname(sourceZeiaRelative)).toLowerCase().replace(/[^a-z0-9]+/g, "-") || "sample";
  return `${SAMPLE_ZEIA_CACHE_RELATIVE}/${stem}`;
}

export async function resolveSampleZeiaSourceFiles(
  projectRoot: string,
  progress: ZeiaMiningProgress = {}
): Promise<ZeiaSampleFile[]> {
  const sourceZeiaPath = await resolveSampleZeiaRelativePath(projectRoot);
  if (!sourceZeiaPath) return [];

  const cached = await loadSampleZeiaSourceFilesFromCache(projectRoot, sourceZeiaPath);
  if (cached) {
    progress.onPhase?.("Loaded ZEIA sample cache", 90);
    return cached;
  }

  progress.onPhase?.("Opening ZEIA sample archive", 38);
  const mined = await mineSampleZeiaSourceFiles(projectRoot, sourceZeiaPath, progress);
  if (!mined.length) return mined;

  progress.onPhase?.("Writing ZEIA sample cache", 88);
  await writeSampleZeiaSampleCache(projectRoot, sourceZeiaPath, mined);
  progress.onPhase?.("ZEIA sample source files ready", 90);
  return mined;
}

export async function buildSampleZeiaSampleCache(projectRoot: string, options: { force?: boolean } = {}): Promise<{
  cacheDir: string;
  files: ZeiaSampleFile[];
}> {
  const sourceZeiaPath = await resolveSampleZeiaRelativePath(projectRoot);
  if (!sourceZeiaPath) {
    throw new Error(
      `No sample ZEIA found. Set ${SAMPLE_ZEIA_ENV} or place a .zeia under ready-to-import/*/source/original-sources/.`
    );
  }
  const cacheRelative = sampleZeiaCacheRelative(sourceZeiaPath);
  if (options.force) {
    await fs.rm(resolveProjectPath(projectRoot, cacheRelative), { recursive: true, force: true });
  }

  const mined = await mineSampleZeiaSourceFiles(projectRoot, sourceZeiaPath, {
    onPhase: (message) => console.log(message)
  });
  const cacheDir = await writeSampleZeiaSampleCache(projectRoot, sourceZeiaPath, mined);
  return { cacheDir, files: mined };
}

async function loadSampleZeiaSourceFilesFromCache(
  projectRoot: string,
  sourceZeiaPath: string
): Promise<ZeiaSampleFile[] | null> {
  const manifest = await readValidCacheManifest(projectRoot, sourceZeiaPath);
  if (!manifest) return null;

  const cacheRoot = resolveProjectPath(projectRoot, sampleZeiaCacheRelative(sourceZeiaPath));
  const files: ZeiaSampleFile[] = [];
  for (const entry of manifest.files) {
    const absolutePath = path.join(cacheRoot, entry.relativePath);
    try {
      await fs.access(absolutePath);
    } catch {
      return null;
    }
    files.push({
      id: entry.id,
      kind: entry.kind,
      label: entry.label,
      path: toProjectRelativePath(projectRoot, absolutePath)
    });
  }
  return files;
}

async function readValidCacheManifest(
  projectRoot: string,
  sourceZeiaPath: string
): Promise<ZeiaSampleCacheManifest | null> {
  const cacheRoot = resolveProjectPath(projectRoot, sampleZeiaCacheRelative(sourceZeiaPath));
  const manifestPath = path.join(cacheRoot, "manifest.json");
  let raw: string;
  try {
    raw = await fs.readFile(manifestPath, "utf8");
  } catch {
    return null;
  }

  let manifest: ZeiaSampleCacheManifest;
  try {
    manifest = JSON.parse(raw) as ZeiaSampleCacheManifest;
  } catch {
    return null;
  }

  if (manifest.version !== CACHE_VERSION || manifest.sourceZeiaPath !== sourceZeiaPath || !Array.isArray(manifest.files)) {
    return null;
  }

  const fingerprint = await zeiaSourceFingerprint(projectRoot, sourceZeiaPath);
  if (!fingerprintsMatch(manifest.sourceFingerprint, fingerprint)) return null;
  return manifest;
}

async function mineSampleZeiaSourceFiles(
  projectRoot: string,
  sourceZeiaPath: string,
  progress: ZeiaMiningProgress
): Promise<ZeiaSampleFile[]> {
  try {
    const zip = await loadZipArchive(projectRoot, sourceZeiaPath);
    const entries = Object.values(zip.files).filter((entry) => !entry.dir);

    progress.onPhase?.("Selecting geometry entries", 52);
    const geometryEntries = await selectZeiaGeometryEntries(entries);

    progress.onPhase?.("Selecting mesh entries", 68);
    const meshEntries = await selectZeiaMeshEntries(entries, geometryEntries);

    progress.onPhase?.("Selecting transfer script entries", 82);
    const transferScriptEntries = await selectZeiaTransferScriptEntries(entries, progress);

    return [
      ...transferScriptEntries.map((entry) => archiveSampleFile(entry, sourceZeiaPath, "xscr", "source-zeia-xscr")),
      ...geometryEntries.map((entry) => archiveSampleFile(entry, sourceZeiaPath, "worktable-geometry", "source-zeia-geometry")),
      ...meshEntries.map((entry) => archiveSampleFile(entry, sourceZeiaPath, "worktable-mesh", "source-zeia-mesh"))
    ];
  } catch (error) {
    console.warn(`Could not load ZEIA sample source entries: ${error instanceof Error ? error.message : String(error)}`);
    return [];
  }
}

async function writeSampleZeiaSampleCache(
  projectRoot: string,
  sourceZeiaPath: string,
  archiveFiles: ZeiaSampleFile[]
): Promise<string> {
  const cacheRoot = resolveProjectPath(projectRoot, sampleZeiaCacheRelative(sourceZeiaPath));
  await fs.rm(cacheRoot, { recursive: true, force: true });
  await fs.mkdir(cacheRoot, { recursive: true });

  const zip = await loadZipArchive(projectRoot, sourceZeiaPath);
  const manifestFiles: CachedZeiaSampleFile[] = [];

  for (const file of archiveFiles) {
    if (!file.archivePath || !file.entryPath) continue;
    const entry = zip.file(file.entryPath);
    if (!entry) continue;

    const relativePath = cacheRelativePathForKind(file.kind, file.entryPath);
    const absolutePath = path.join(cacheRoot, relativePath);
    await fs.mkdir(path.dirname(absolutePath), { recursive: true });
    await fs.writeFile(absolutePath, Buffer.from(await entry.async("uint8array")));

    manifestFiles.push({
      id: file.id,
      kind: file.kind,
      label: file.label,
      relativePath: relativePath.replace(/\\/g, "/")
    });
  }

  const manifest: ZeiaSampleCacheManifest = {
    version: CACHE_VERSION,
    sourceZeiaPath,
    sourceFingerprint: await zeiaSourceFingerprint(projectRoot, sourceZeiaPath),
    files: manifestFiles
  };
  await fs.writeFile(path.join(cacheRoot, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  return cacheRoot;
}

function cacheRelativePathForKind(kind: string, entryPath: string): string {
  const kindFolder =
    kind === "xscr" ? "xscr" : kind === "worktable-mesh" ? "worktable-mesh" : "worktable-geometry";
  const normalizedEntry = sanitizeArchiveEntryPath(entryPath);
  return path.join(kindFolder, normalizedEntry);
}

function sanitizeArchiveEntryPath(entryPath: string): string {
  const normalized = entryPath.replace(/\\/g, "/").replace(/^\/+/, "");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.some((segment) => segment === "..")) {
    throw new Error(`Invalid archive entry path: ${entryPath}`);
  }
  return segments.join(path.sep);
}

async function zeiaSourceFingerprint(projectRoot: string, sourceZeiaPath: string): Promise<ZeiaSourceFingerprint> {
  const stat = await fs.stat(resolveProjectPath(projectRoot, sourceZeiaPath));
  return { size: stat.size, mtimeMs: stat.mtimeMs };
}

function fingerprintsMatch(left: ZeiaSourceFingerprint, right: ZeiaSourceFingerprint): boolean {
  return left.size === right.size && left.mtimeMs === right.mtimeMs;
}

async function loadZipArchive(projectRoot: string, samplePath: string): Promise<JSZip> {
  const body = await fs.readFile(resolveProjectPath(projectRoot, samplePath));
  return JSZip.loadAsync(body);
}

async function selectZeiaTransferScriptEntries(
  entries: ArchiveEntry[],
  progress: ZeiaMiningProgress
): Promise<ArchiveEntry[]> {
  const xscrEntries = sortArchiveEntries(entries.filter((entry) => extensionForPath(entry.name) === ".xscr"));
  const matchingEntries: Array<ArchiveEntry | null> = [];
  for (let index = 0; index < xscrEntries.length; index += 1) {
    const entry = xscrEntries[index];
    progress.onTransferScriptScan?.(index, xscrEntries.length);
    const text = await entry.async("text");
    matchingEntries.push(text.includes("TransferLabware") ? entry : null);
  }
  if (xscrEntries.length > 0) {
    progress.onTransferScriptScan?.(xscrEntries.length, xscrEntries.length);
  }
  return matchingEntries.filter((entry): entry is ArchiveEntry => Boolean(entry));
}

async function selectZeiaGeometryEntries(entries: ArchiveEntry[]): Promise<ArchiveEntry[]> {
  const workspaceEntries = sortArchiveEntries(entries.filter((entry) => extensionForPath(entry.name) === ".xwsp"));
  const componentEntries = sortArchiveEntries(entries.filter((entry) => extensionForPath(entry.name) === ".xcmp"));
  const siteEntries = sortArchiveEntries(entries.filter((entry) => extensionForPath(entry.name) === ".xsit"));
  const primaryEntries = dedupeArchiveEntries([...workspaceEntries, ...componentEntries, ...siteEntries]);
  const referencedConnectorGuids = await referencedConnectorGuidsFromEntries(primaryEntries);
  const connectorEntries = selectConnectorEntries(entries, referencedConnectorGuids);
  return dedupeArchiveEntries([...primaryEntries, ...connectorEntries]);
}

async function selectZeiaMeshEntries(entries: ArchiveEntry[], geometryEntries: ArchiveEntry[]): Promise<ArchiveEntry[]> {
  const geometryTexts = await Promise.all(geometryEntries.map((entry) => entry.async("text")));
  const referencedMeshGuids = referencedMeshGuidsFromTexts(geometryTexts);
  return selectArchiveMeshEntries(
    entries,
    referencedMeshGuids,
    geometryEntries.map((entry) => entry.name)
  );
}

function archiveSampleFile(entry: ArchiveEntry, sourceZeiaPath: string, kind: string, idPrefix: string): ZeiaSampleFile {
  return {
    id: `${idPrefix}-${hashText(entry.name)}`,
    kind,
    label: archiveDisplayName(entry.name),
    path: `${sourceZeiaPath}::${entry.name}`,
    archivePath: sourceZeiaPath,
    entryPath: entry.name
  };
}

async function referencedConnectorGuidsFromEntries(entries: ArchiveEntry[]): Promise<Set<string>> {
  const out = new Set<string>();
  await Promise.all(
    entries.map(async (entry) => {
      const text = await entry.async("text");
      const connectorGuidPattern = /ConnectorTemplateGuid[^>]*>\s*([^<]*)/gi;
      for (const match of text.matchAll(connectorGuidPattern)) {
        const guid = normalizeGuid(match[1] || "");
        if (isRealGuid(guid)) out.add(guid);
      }
    })
  );
  return out;
}

function selectConnectorEntries(entries: ArchiveEntry[], referencedConnectorGuids: Set<string>): ArchiveEntry[] {
  const connectorEntries = sortArchiveEntries(entries.filter((entry) => extensionForPath(entry.name) === ".xcon"));
  if (!connectorEntries.length) return [];

  const referencedEntries = connectorEntries.filter((entry) => referencedConnectorGuids.has(normalizeGuid(stemForPath(entry.name))));
  if (referencedEntries.length) return referencedEntries.slice(0, MAX_ZEIA_CONNECTOR_ENTRIES);

  return connectorEntries.slice(0, MAX_ZEIA_FALLBACK_CONNECTOR_ENTRIES);
}

function sortArchiveEntries(entries: ArchiveEntry[]): ArchiveEntry[] {
  return [...entries].sort((a, b) => a.name.localeCompare(b.name));
}

function dedupeArchiveEntries(entries: ArchiveEntry[]): ArchiveEntry[] {
  const seen = new Set<string>();
  const out: ArchiveEntry[] = [];
  entries.forEach((entry) => {
    const key = normalizeArchiveName(entry.name);
    if (seen.has(key)) return;
    seen.add(key);
    out.push(entry);
  });
  return out;
}

function normalizeArchiveName(name: string): string {
  return name.replace(/\\/g, "/").toLowerCase();
}

function extensionForPath(filePath: string): string {
  const cleanName = filePath.split(/[\\/]/).pop() || filePath;
  const index = cleanName.lastIndexOf(".");
  return index >= 0 ? cleanName.slice(index).toLowerCase() : "";
}

function stemForPath(filePath: string): string {
  const cleanName = filePath.split(/[\\/]/).pop() || filePath;
  const index = cleanName.lastIndexOf(".");
  return index >= 0 ? cleanName.slice(0, index) : cleanName;
}

function normalizeGuid(value: string): string {
  const match = value.match(GUID_IN_TEXT_PATTERN);
  return match?.[0].toLowerCase() || "";
}

function isRealGuid(value: string): boolean {
  return Boolean(value && value !== EMPTY_GUID);
}

function archiveDisplayName(name: string): string {
  const parts = name.split(/[\\/]/).filter(Boolean);
  return parts.slice(-3).join("/");
}

function toProjectRelativePath(projectRoot: string, absolutePath: string): string {
  return path.relative(projectRoot, absolutePath).replace(/\\/g, "/");
}
