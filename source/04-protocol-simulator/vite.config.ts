import react from "@vitejs/plugin-react";
import JSZip from "jszip";
import fs from "node:fs/promises";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

const appRoot = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(appRoot, "../..");
const launchBundlePath = process.env.TECAN_SIMULATOR_BUNDLE ? path.resolve(process.env.TECAN_SIMULATOR_BUNDLE) : "";

const LAUNCH_BUNDLE_SAMPLE_ID = "launch-bundle";
const MAX_LAUNCH_BUNDLE_FILES = 240;
const TEXT_EXTENSIONS = new Set([".xscr", ".gwl", ".yaml", ".yml", ".md", ".txt", ".xwsp", ".xcmp", ".xcon", ".xsit", ".xmsh"]);
const XML_CONTENT_TYPES = new Set([".xscr", ".xwsp", ".xcmp", ".xcon", ".xsit", ".xmsh"]);
const IMAGE_CONTENT_TYPES: Record<string, string> = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".gif": "image/gif",
  ".webp": "image/webp"
};
const MODEL_CONTENT_TYPES: Record<string, string> = {
  ".glb": "model/gltf-binary",
  ".gltf": "model/gltf+json",
  ".bin": "application/octet-stream"
};
const ARCHIVE_CONTENT_TYPES: Record<string, string> = {
  ".zip": "application/zip",
  ".zeia": "application/zip"
};
const LAUNCH_BUNDLE_EXTENSIONS = new Set([
  ".json",
  ".xscr",
  ".gwl",
  ".zeia",
  ".zip",
  ".yaml",
  ".yml",
  ".md",
  ".xwsp",
  ".xcmp",
  ".xcon",
  ".xsit",
  ".xmsh",
  ".jpg",
  ".jpeg",
  ".png",
  ".gif",
  ".webp"
]);

type SampleFile = {
  id: string;
  kind: string;
  label: string;
  path: string;
  archivePath?: string;
  entryPath?: string;
};

type SampleDataset = {
  id: string;
  name: string;
  description: string;
  files: SampleFile[];
};

const zipCache = new Map<string, Promise<JSZip>>();
let samplesCache: Promise<SampleDataset[]> | null = null;

function invalidateSampleCaches(): void {
  samplesCache = null;
  zipCache.clear();
}

function sampleRoutePlugin(): Plugin {
  return {
    name: "tecan-sample-route",
    handleHotUpdate({ file }) {
      // Sample artifacts/archives are read and cached on first request. Drop the
      // caches when a relevant source file changes so the dev server serves fresh
      // content without requiring a manual restart.
      const extension = extensionForPath(file);
      if (LAUNCH_BUNDLE_EXTENSIONS.has(extension)) invalidateSampleCaches();
    },
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (!req.url?.startsWith("/api/samples")) {
          next();
          return;
        }

        try {
          const url = new URL(req.url, "http://localhost");
          const samples = await resolveSamples();
          if (url.pathname === "/api/samples") {
            res.setHeader("Content-Type", "application/json");
            res.end(JSON.stringify(samples));
            return;
          }

          const match = url.pathname.match(/^\/api\/samples\/([^/]+)\/([^/]+)$/);
          if (!match) {
            next();
            return;
          }

          const datasetId = decodeURIComponent(match[1]);
          const fileId = decodeURIComponent(match[2]);
          const dataset = samples.find((item) => item.id === datasetId);
          const file = dataset?.files.find((item) => item.id === fileId);
          if (!dataset || !file) {
            res.statusCode = 404;
            res.end("Sample file not found");
            return;
          }

          const body = await readSampleFile(file);
          res.setHeader("Content-Type", contentTypeForPath(file.entryPath || file.path));
          res.setHeader("Content-Length", String(body.byteLength));
          res.end(body);
        } catch (error) {
          res.statusCode = 500;
          res.end(error instanceof Error ? error.message : "Unknown sample route error");
        }
      });
    }
  };
}

function resolveSamples(): Promise<SampleDataset[]> {
  samplesCache ||= buildSamples();
  return samplesCache;
}

async function buildSamples(): Promise<SampleDataset[]> {
  // Discover-only: TECAN_SIMULATOR_BUNDLE + ready-to-import/* — no hardcoded demos.
  const [launchBundle, discoveredBundles] = await Promise.all([
    createLaunchBundleSample(),
    discoverReadyToImportSamples()
  ]);
  const seen = new Set<string>();
  const samples: SampleDataset[] = [];
  for (const dataset of [launchBundle, ...discoveredBundles]) {
    if (!dataset || seen.has(dataset.id)) continue;
    seen.add(dataset.id);
    samples.push(dataset);
  }
  return samples;
}

const READY_BUNDLE_CANDIDATES: Array<{ id: string; kind: string; label: string; relativeParts: string[] }> = [
  { id: "protocol", kind: "protocol-ir", label: "protocol.ir.json", relativeParts: ["source", "protocol.ir.json"] },
  {
    id: "xscr",
    kind: "xscr",
    label: "generated_script.xscr",
    relativeParts: ["direct-imports", "scripts", "full-script", "generated_script.xscr"]
  },
  { id: "gwl", kind: "gwl", label: "generated_worklist.gwl", relativeParts: ["source", "generated_worklist.gwl"] },
  { id: "metadata", kind: "metadata", label: "metadata.json", relativeParts: ["source", "metadata.json"] },
  {
    id: "hardware",
    kind: "hardware",
    label: "hardware_manifest.json",
    relativeParts: ["source", "hardware", "hardware_manifest.json"]
  },
  {
    id: "worktable-patch",
    kind: "worktable-diff",
    label: "worktable.patch.json",
    relativeParts: ["source", "worktable.patch.json"]
  },
  {
    id: "validation-diff",
    kind: "validation-diff",
    label: "validation_diff.json",
    relativeParts: ["source", "validation_diff.json"]
  }
];

async function discoverReadyToImportSamples(): Promise<SampleDataset[]> {
  const readyRoot = path.join(projectRoot, "ready-to-import");
  let entries: Array<{ name: string; isDirectory: () => boolean }>;
  try {
    entries = await fs.readdir(readyRoot, { withFileTypes: true });
  } catch {
    return [];
  }

  const datasets: SampleDataset[] = [];
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (!entry.isDirectory() || entry.name.startsWith(".") || entry.name.startsWith("_")) continue;
    const bundleRoot = path.join(readyRoot, entry.name);
    const files: SampleFile[] = [];
    for (const candidate of READY_BUNDLE_CANDIDATES) {
      const relativePath = path.posix.join("ready-to-import", entry.name, ...candidate.relativeParts);
      const absolutePath = path.join(projectRoot, ...relativePath.split("/"));
      try {
        await fs.access(absolutePath);
      } catch {
        continue;
      }
      files.push({
        id: candidate.id,
        kind: candidate.kind,
        label: candidate.label,
        path: relativePath
      });
    }

    // Also surface a local ZEIA if present (geometry/mesh mining still optional via cache).
    for (const zeiaRel of [
      ["source", "original-sources"],
      ["original_sources"],
      ["source", "original_sources"]
    ]) {
      const zeiaDir = path.join(bundleRoot, ...zeiaRel);
      let names: string[] = [];
      try {
        names = await fs.readdir(zeiaDir);
      } catch {
        continue;
      }
      for (const name of names.sort()) {
        if (!name.toLowerCase().endsWith(".zeia")) continue;
        files.push({
          id: `zeia-${hashText(name)}`,
          kind: "zeia",
          label: name,
          path: path.posix.join("ready-to-import", entry.name, ...zeiaRel, name)
        });
      }
    }

    if (!files.some((file) => file.kind === "protocol-ir" || file.kind === "xscr" || file.kind === "zeia")) {
      continue;
    }

    datasets.push({
      id: `bundle-${entry.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`,
      name: entry.name,
      description: `Discovered ready-to-import bundle (${files.length} artifacts).`,
      files
    });
  }
  return datasets;
}

async function createLaunchBundleSample(): Promise<SampleDataset | null> {
  if (!launchBundlePath) return null;

  try {
    const stats = await fs.stat(launchBundlePath);
    const files = stats.isDirectory()
      ? await discoverLaunchBundleFiles(launchBundlePath)
      : isLaunchBundleFile(launchBundlePath)
        ? [launchBundlePath]
        : [];
    if (!files.length) {
      console.warn(`Launch bundle has no simulator-readable artifacts: ${launchBundlePath}`);
      return null;
    }

    const bundleName = stats.isDirectory() ? path.basename(launchBundlePath) : stemForPath(launchBundlePath);
    return {
      id: LAUNCH_BUNDLE_SAMPLE_ID,
      name: `Launch Bundle: ${bundleName}`,
      description: "Protocol artifacts loaded from TECAN_SIMULATOR_BUNDLE.",
      files: files.slice(0, MAX_LAUNCH_BUNDLE_FILES).map((filePath) => launchBundleSampleFile(filePath))
    };
  } catch (error) {
    console.warn(`Could not load launch bundle ${launchBundlePath}: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

async function discoverLaunchBundleFiles(root: string): Promise<string[]> {
  const found: string[] = [];

  async function visit(directory: string): Promise<void> {
    if (found.length >= MAX_LAUNCH_BUNDLE_FILES) return;
    const entries = await fs.readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      if (found.length >= MAX_LAUNCH_BUNDLE_FILES) break;
      if (entry.name.startsWith(".") || entry.name === "node_modules" || entry.name === "__pycache__") continue;
      const absolutePath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(absolutePath);
      } else if (entry.isFile() && isLaunchBundleFile(absolutePath)) {
        found.push(absolutePath);
      }
    }
  }

  await visit(root);
  return found.sort((a, b) => launchBundlePriority(a) - launchBundlePriority(b) || launchBundleLabel(a).localeCompare(launchBundleLabel(b)));
}

function launchBundleSampleFile(filePath: string): SampleFile {
  const label = launchBundleLabel(filePath);
  return {
    id: `launch-${hashText(filePath)}`,
    kind: launchBundleKind(filePath),
    label,
    path: path.resolve(filePath)
  };
}

function launchBundleLabel(filePath: string): string {
  const absolutePath = path.resolve(filePath);
  if (launchBundlePath && isWithinPath(absolutePath, launchBundlePath)) {
    return path.relative(launchBundlePath, absolutePath).replace(/\\/g, "/") || path.basename(absolutePath);
  }
  return path.basename(absolutePath);
}

function launchBundleKind(filePath: string): string {
  const normalized = normalizeArchiveName(filePath);
  const extension = extensionForPath(filePath);
  if (normalized.endsWith("protocol.ir.json")) return "protocol-ir";
  if (normalized.includes("simulation") && extension === ".json") return "simulation";
  if (normalized.endsWith("hardware_manifest.json")) return "hardware";
  if (normalized.endsWith("metadata.json")) return "metadata";
  if (normalized.includes("worktable") && extension === ".json") return "worktable-diff";
  if (normalized.includes("validation")) return "validation-diff";
  if (normalized.includes("repair")) return "repair-plan";
  if (extension === ".xscr") return "xscr";
  if (extension === ".gwl") return "gwl";
  if (extension === ".zeia" || extension === ".zip") return "zeia";
  if (extension === ".xwsp" || extension === ".xcmp" || extension === ".xcon" || extension === ".xsit") return "worktable-geometry";
  if (extension === ".xmsh") return "worktable-mesh";
  if (IMAGE_CONTENT_TYPES[extension]) return "hardware-image";
  if (extension === ".yaml" || extension === ".yml") return "alias-map";
  return "metadata";
}

function launchBundlePriority(filePath: string): number {
  const kind = launchBundleKind(filePath);
  const order = [
    "protocol-ir",
    "xscr",
    "gwl",
    "simulation",
    "metadata",
    "hardware",
    "worktable-diff",
    "validation-diff",
    "repair-plan",
    "alias-map",
    "worktable-geometry",
    "worktable-mesh",
    "hardware-image",
    "zeia"
  ];
  const index = order.indexOf(kind);
  return index >= 0 ? index : order.length;
}

function isLaunchBundleFile(filePath: string): boolean {
  return LAUNCH_BUNDLE_EXTENSIONS.has(extensionForPath(filePath));
}

async function readSampleFile(file: SampleFile): Promise<Buffer> {
  if (file.archivePath && file.entryPath) {
    const zip = await loadZipArchive(file.archivePath);
    const entry = zip.file(file.entryPath);
    if (!entry) throw new Error(`Archive entry not found: ${file.entryPath}`);
    return Buffer.from(await entry.async("uint8array"));
  }

  const absolutePath = resolveSamplePath(file.path);
  return fs.readFile(absolutePath);
}

function loadZipArchive(samplePath: string): Promise<JSZip> {
  const absolutePath = resolveSamplePath(samplePath);
  const key = normalizeArchiveName(absolutePath);
  const cached = zipCache.get(key);
  if (cached) return cached;

  const loaded = fs.readFile(absolutePath).then((body) => JSZip.loadAsync(body));
  zipCache.set(key, loaded);
  return loaded;
}

function resolveSamplePath(samplePath: string): string {
  if (path.isAbsolute(samplePath)) {
    const absolutePath = path.resolve(samplePath);
    if (isWithinProjectRoot(absolutePath) || (launchBundlePath && isWithinPath(absolutePath, launchBundlePath))) {
      return absolutePath;
    }
    throw new Error("Path outside allowed sample roots");
  }
  return resolveProjectPath(samplePath);
}

function resolveProjectPath(relativePath: string): string {
  const absolutePath = path.resolve(projectRoot, relativePath);
  if (!isWithinProjectRoot(absolutePath)) throw new Error("Path outside project root");
  return absolutePath;
}

function isWithinProjectRoot(absolutePath: string): boolean {
  return isWithinPath(absolutePath, projectRoot);
}

function isWithinPath(absolutePath: string, rootPath: string): boolean {
  const root = path.resolve(rootPath);
  const target = path.resolve(absolutePath);
  if (target === root) return true;
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function contentTypeForPath(filePath: string): string {
  const extension = extensionForPath(filePath);
  if (extension === ".json") return "application/json";
  if (XML_CONTENT_TYPES.has(extension)) return "application/xml";

  const imageContentType = IMAGE_CONTENT_TYPES[extension];
  if (imageContentType) return imageContentType;

  const modelContentType = MODEL_CONTENT_TYPES[extension];
  if (modelContentType) return modelContentType;

  const archiveContentType = ARCHIVE_CONTENT_TYPES[extension];
  if (archiveContentType) return archiveContentType;

  if (TEXT_EXTENSIONS.has(extension)) return "text/plain";
  return "application/octet-stream";
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

function hashText(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function terminalRefreshShortcutPlugin(): Plugin {
  return {
    name: "tecan-terminal-refresh-shortcut",
    apply: "serve",
    configureServer(server) {
      if (!process.stdin.isTTY) return;
      readline.emitKeypressEvents(process.stdin);
      if (typeof process.stdin.setRawMode === "function" && !process.stdin.isRaw) {
        process.stdin.setRawMode(true);
      }

      const handleKeypress = (_input: string, key: readline.Key) => {
        if (key.ctrl && key.name === "c") {
          process.kill(process.pid, "SIGINT");
          return;
        }
        if (key.name !== "r" || key.ctrl || key.meta || key.shift) return;
        server.ws.send({ type: "full-reload", path: "*" });
        server.config.logger.info("Reloaded browser clients from terminal shortcut: r");
      };

      process.stdin.on("keypress", handleKeypress);
      server.httpServer?.once("close", () => {
        process.stdin.off("keypress", handleKeypress);
      });
    }
  };
}

export default defineConfig({
  plugins: [react(), sampleRoutePlugin(), terminalRefreshShortcutPlugin()],
  server: {
    host: process.env.TECAN_SIMULATOR_HOST || "127.0.0.1",
    port: Number.parseInt(process.env.TECAN_SIMULATOR_PORT || "5173", 10) || 5173,
    strictPort: process.env.TECAN_SIMULATOR_STRICT_PORT === "1",
    open: true
  },
  preview: {
    host: process.env.TECAN_SIMULATOR_HOST || "127.0.0.1",
    port: Number.parseInt(process.env.TECAN_SIMULATOR_PORT || "5173", 10) || 5173,
    strictPort: process.env.TECAN_SIMULATOR_STRICT_PORT === "1",
    open: true
  }
});
