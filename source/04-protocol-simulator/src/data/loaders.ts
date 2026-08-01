import JSZip from "jszip";
import type { ArtifactKind, SampleDataset, SourceArtifact } from "../types";
import {
  composeMultiFileProgressReporter,
  createLoadProgressReporter,
  type LoadProgressReporter,
  type RegistryStartupProgress
} from "./loadProgress";
import { detectArtifactKind } from "./parsers";
import { referencedMeshGuidsFromTexts, selectArchiveMeshEntries } from "./meshArchiveSelection";

const MAX_ARCHIVE_TEXT_ENTRIES = 800;
const MAX_ARCHIVE_CONNECTOR_ENTRIES = 4000;
const MAX_ARCHIVE_FALLBACK_CONNECTOR_ENTRIES = 200;
const MAX_ARCHIVE_IMAGE_ENTRIES = 32;
const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp"]);
const EMPTY_GUID = "00000000-0000-0000-0000-000000000000";
const GUID_IN_TEXT_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const WORKTABLE_MESH_KIND = "worktable-mesh" as ArtifactKind;

type ArchiveEntry = JSZip.JSZipObject;

export async function fetchSamples(): Promise<SampleDataset[]> {
  const response = await fetch("/api/samples");
  if (!response.ok) throw new Error(`Could not load samples: ${response.status}`);
  return (await response.json()) as SampleDataset[];
}

export async function fetchRegistryStartupProgress(): Promise<RegistryStartupProgress> {
  const response = await fetch("/api/startup-progress");
  if (!response.ok) throw new Error(`Could not load startup progress: ${response.status}`);
  return (await response.json()) as RegistryStartupProgress;
}

export type { LoadProgressReporter, RegistryStartupProgress, ZeiaLoadProgress } from "./loadProgress";

export async function loadSample(dataset: SampleDataset, onProgress?: LoadProgressReporter): Promise<SourceArtifact[]> {
  const artifactGroups = await Promise.all(
    dataset.files.map(async (file, fileIndex) => {
      const fileProgress = composeMultiFileProgressReporter(onProgress, fileIndex, dataset.files.length, file.label || file.path || file.id);
      const response = await fetch(`/api/samples/${encodeURIComponent(dataset.id)}/${encodeURIComponent(file.id)}`);
      if (!response.ok) throw new Error(`Could not load ${file.label}: ${response.status}`);
      const declaredKind = file.kind as ArtifactKind;
      const kind: ArtifactKind = isImagePath(file.path) || declaredKind === "hardware-image" ? "hardware-image" : declaredKind;

      if (isArchivePath(file.path) || isArchivePath(file.label) || kind === "zeia") {
        const blob = await response.blob();
        const archiveFile = new File([blob], file.label || file.path || file.id, { type: blob.type || "application/zip" });
        return loadArchive(archiveFile, dataset.name, fileProgress);
      }

      if (kind === "hardware-image") {
        const blob = await response.blob();
        return [
          {
            id: `${dataset.id}-${file.id}`,
            name: file.label,
            kind,
            source: dataset.name,
            text: "",
            size: blob.size,
            dataUrl: await blobToDataUrl(blob),
            mimeType: blob.type || mimeTypeForPath(file.path),
            path: file.path
          }
        ];
      }

      const text = await response.text();
      const expanded = expandSimulatorHandoffText({
        id: `${dataset.id}-${file.id}`,
        name: file.label,
        source: dataset.name,
        text,
        path: file.path || file.label,
        lastModified: 0,
      });
      if (expanded) return expanded;
      return [sourceArtifactForTextEntry({
        id: `${dataset.id}-${file.id}`,
        name: file.label,
        source: dataset.name,
        text,
        size: text.length,
        path: file.path || file.label
      })];
    })
  );
  return artifactGroups.flat();
}

export async function loadFiles(files: FileList | File[], onProgress?: LoadProgressReporter): Promise<SourceArtifact[]> {
  const fileArray = Array.from(files).filter(Boolean);
  const artifacts: SourceArtifact[] = [];
  for (let fileIndex = 0; fileIndex < fileArray.length; fileIndex += 1) {
    const file = fileArray[fileIndex];
    const fileProgress = composeMultiFileProgressReporter(onProgress, fileIndex, fileArray.length, file.name);
    if (isImagePath(file.name)) {
      artifacts.push(await loadImageFile(file));
      continue;
    }

    if (isArchivePath(file.name)) {
      artifacts.push(...(await loadArchive(file, file.name, fileProgress)));
      continue;
    }

    const text = await file.text();
    const expanded = expandSimulatorHandoffFile(file, text);
    if (expanded) {
      artifacts.push(...expanded);
      continue;
    }

    artifacts.push(sourceArtifactForTextEntry({
      id: `${file.name}-${file.lastModified}`,
      name: file.name,
      source: "upload",
      text,
      size: file.size,
      path: file.name
    }));
  }
  return artifacts;
}

function expandSimulatorHandoffFile(file: File, text: string): SourceArtifact[] | null {
  return expandSimulatorHandoffText({
    id: `${file.name}-${file.lastModified}`,
    name: file.name,
    source: "upload",
    text,
    path: file.name,
    lastModified: file.lastModified
  });
}

type SimulatorHandoffInput = {
  id: string;
  name: string;
  source: string;
  text: string;
  path: string;
  lastModified: number;
};

function expandSimulatorHandoffText(input: SimulatorHandoffInput): SourceArtifact[] | null {
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(input.text) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (payload.app !== "tecan-protocol-simulator") return null;
  if (payload.kind !== "simulator-project" && payload.kind !== "sim-scene") return null;

  const artifacts: SourceArtifact[] = [
    sourceArtifactForTextEntry({
      id: input.id,
      name: input.name,
      source: input.source,
      text: input.text,
      size: input.text.length,
      path: input.path
    })
  ];

  if (payload.kind !== "simulator-project" || !Array.isArray(payload.artifacts)) {
    return artifacts;
  }

  payload.artifacts.forEach((entry, index) => {
    if (!entry || typeof entry !== "object") return;
    const record = entry as Record<string, unknown>;
    const embeddedText = typeof record.text === "string" ? record.text.trim() : "";
    if (!embeddedText) return;
    const bundlePath = typeof record.path === "string" ? record.path : "";
    const name = typeof record.name === "string" ? record.name : bundlePath || `artifact-${index + 1}`;
    const artifactId = typeof record.id === "string" ? record.id : `${input.name}:${bundlePath || name}`;
    artifacts.push(
      sourceArtifactForTextEntry({
        id: `${input.id}:${artifactId}`,
        name,
        source: input.source,
        text: embeddedText,
        size: embeddedText.length,
        path: bundlePath || name
      })
    );
  });

  return artifacts;
}

async function loadArchive(file: File, source = file.name, onProgress?: LoadProgressReporter): Promise<SourceArtifact[]> {
  const progress = createLoadProgressReporter(onProgress, file.name);

  progress.reportPhase("reading", "Reading ZEIA archive", 0, 0, 1);
  const buffer = await file.arrayBuffer();
  progress.reportPhase("reading", "Reading ZEIA archive", 1, 1, 1);

  progress.reportPhase("opening", "Opening ZEIA package", 0, 0, 1);
  const zip = await JSZip.loadAsync(buffer);
  progress.reportPhase("opening", "Opened ZEIA package", 1, 1, 1);

  const entries = Object.values(zip.files).filter((entry) => !entry.dir);
  const primaryTextEntries = selectPrimaryArchiveTextEntries(entries);
  const primaryTextArtifacts = await loadTextArtifacts(file, source, primaryTextEntries, (completed, total, entryName) => {
    progress.reportPhase("primary", "Extracting protocol files", total ? completed / total : 1, completed, total, entryName);
  });
  const referencedConnectorGuids = referencedConnectorGuidsFromArtifacts(primaryTextArtifacts);
  const connectorEntries = selectConnectorEntries(entries, referencedConnectorGuids, primaryTextEntries);
  const connectorArtifacts = await loadTextArtifacts(file, source, connectorEntries, (completed, total, entryName) => {
    progress.reportPhase("connectors", "Extracting connector geometry", total ? completed / total : 1, completed, total, entryName);
  });
  const referencedMeshGuids = referencedMeshGuidsFromArtifacts([...primaryTextArtifacts, ...connectorArtifacts]);
  const meshEntries = selectMeshEntries(entries, referencedMeshGuids, [...primaryTextEntries, ...connectorEntries]);
  const meshArtifacts = await loadTextArtifacts(file, source, meshEntries, (completed, total, entryName) => {
    progress.reportPhase("meshes", "Extracting worktable meshes", total ? completed / total : 1, completed, total, entryName);
  });
  const imageEntries = selectImageEntries(entries);
  const imageArtifacts = await loadImageArtifacts(file, source, imageEntries, (completed, total, entryName) => {
    progress.reportPhase("images", "Extracting hardware images", total ? completed / total : 1, completed, total, entryName);
  });

  progress.reportPhase("finalizing", "Finalizing import", 0.2, 0, 1);
  const artifacts = [...primaryTextArtifacts, ...connectorArtifacts, ...meshArtifacts, ...imageArtifacts].filter((artifact) => artifact.kind !== "unknown");

  if (entries.some((entry) => isWorktableGeometryEntry(entry.name)) && !entries.some((entry) => extensionForPath(entry.name) === ".xwsp")) {
    console.warn(`Archive ${file.name} contains Fluent worktable geometry, but no .xwsp workspace file was found.`);
  }

  const archiveMeshCount = entries.filter((entry) => isWorktableMeshEntry(entry.name)).length;
  const skippedMeshCount = Math.max(0, archiveMeshCount - meshEntries.length);
  if (skippedMeshCount > 0) {
    console.info(
      `Archive ${file.name}: loaded ${meshEntries.length} referenced mesh${meshEntries.length === 1 ? "" : "es"}; ` +
        `skipped ${skippedMeshCount} unreferenced mesh${skippedMeshCount === 1 ? "" : "es"} (resolve via fluent registry).`
    );
  }

  if (referencedMeshGuids.size && !meshArtifacts.length) {
    console.warn(`Archive ${file.name} references Fluent worktable meshes, but no .xmsh mesh entries were loaded.`);
  }

  if (!artifacts.length) {
    artifacts.push({
      id: file.name,
      name: file.name,
      kind: "zeia",
      source,
      text: JSON.stringify(
        {
          archive: file.name,
          entries: Object.keys(zip.files).slice(0, MAX_ARCHIVE_TEXT_ENTRIES)
        },
        null,
        2
      ),
      size: file.size
    });
  }

  progress.complete(`Imported ${artifacts.length.toLocaleString()} artifact${artifacts.length === 1 ? "" : "s"}`);
  return artifacts;
}

type EntryProgressCallback = (completed: number, total: number, entryName: string) => void;

async function loadTextArtifacts(
  file: File,
  source: string,
  entries: ArchiveEntry[],
  onEntryProgress?: EntryProgressCallback
): Promise<SourceArtifact[]> {
  if (!entries.length) {
    onEntryProgress?.(0, 0, "");
    return [];
  }

  const artifacts: SourceArtifact[] = [];
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const text = await entry.async("text");
    artifacts.push(
      sourceArtifactForTextEntry({
        id: `${file.name}:${entry.name}`,
        name: archiveDisplayName(entry.name),
        source,
        text,
        size: text.length,
        path: entry.name
      })
    );
    onEntryProgress?.(index + 1, entries.length, archiveDisplayName(entry.name));
  }
  return artifacts;
}

async function loadImageArtifacts(
  file: File,
  source: string,
  entries: ArchiveEntry[],
  onEntryProgress?: EntryProgressCallback
): Promise<SourceArtifact[]> {
  if (!entries.length) {
    onEntryProgress?.(0, 0, "");
    return [];
  }

  const artifacts: SourceArtifact[] = [];
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const bytes = await entry.async("uint8array");
    const mimeType = mimeTypeForPath(entry.name);
    artifacts.push({
      id: `${file.name}:${entry.name}`,
      name: archiveDisplayName(entry.name),
      kind: "hardware-image" as const,
      source,
      text: "",
      size: bytes.byteLength,
      dataUrl: bytesToDataUrl(bytes, mimeType),
      mimeType,
      path: entry.name
    });
    onEntryProgress?.(index + 1, entries.length, archiveDisplayName(entry.name));
  }
  return artifacts;
}

async function loadImageFile(file: File): Promise<SourceArtifact> {
  return {
    id: `${file.name}-${file.lastModified}`,
    name: file.name,
    kind: "hardware-image",
    source: "upload",
    text: "",
    size: file.size,
    dataUrl: await blobToDataUrl(file),
    mimeType: file.type || mimeTypeForPath(file.name),
    path: file.name
  };
}

function selectPrimaryArchiveTextEntries(entries: ArchiveEntry[]): ArchiveEntry[] {
  const protocolEntries = sortArchiveEntries(entries.filter((entry) => isProtocolArchiveTextEntry(entry.name)));
  const workspaceEntries = sortArchiveEntries(entries.filter((entry) => extensionForPath(entry.name) === ".xwsp"));
  const componentEntries = sortArchiveEntries(entries.filter((entry) => extensionForPath(entry.name) === ".xcmp"));
  const siteEntries = sortArchiveEntries(entries.filter((entry) => extensionForPath(entry.name) === ".xsit"));
  const metadataEntries = sortArchiveEntries(entries.filter((entry) => isRelevantMetadataArchiveTextEntry(entry.name))).slice(0, MAX_ARCHIVE_TEXT_ENTRIES);

  return dedupeArchiveEntries([...protocolEntries, ...workspaceEntries, ...componentEntries, ...siteEntries, ...metadataEntries]);
}

function selectConnectorEntries(entries: ArchiveEntry[], referencedConnectorGuids: Set<string>, primaryTextEntries: ArchiveEntry[]): ArchiveEntry[] {
  const primaryNames = new Set(primaryTextEntries.map((entry) => normalizeArchiveName(entry.name)));
  const connectorEntries = sortArchiveEntries(
    entries.filter((entry) => extensionForPath(entry.name) === ".xcon" && !primaryNames.has(normalizeArchiveName(entry.name)))
  );

  if (!connectorEntries.length) return [];

  const referencedEntries = connectorEntries.filter((entry) => referencedConnectorGuids.has(normalizeGuid(stemForPath(entry.name))));
  if (referencedEntries.length) return referencedEntries.slice(0, MAX_ARCHIVE_CONNECTOR_ENTRIES);

  return connectorEntries.slice(0, MAX_ARCHIVE_FALLBACK_CONNECTOR_ENTRIES);
}

function selectMeshEntries(entries: ArchiveEntry[], referencedMeshGuids: Set<string>, selectedTextEntries: ArchiveEntry[]): ArchiveEntry[] {
  const selectedNames = selectedTextEntries.map((entry) => entry.name);
  return selectArchiveMeshEntries(entries, referencedMeshGuids, selectedNames);
}

function selectImageEntries(entries: ArchiveEntry[]): ArchiveEntry[] {
  return sortArchiveEntries(entries.filter((entry) => isImagePath(entry.name))).slice(0, MAX_ARCHIVE_IMAGE_ENTRIES);
}

function referencedConnectorGuidsFromArtifacts(artifacts: SourceArtifact[]): Set<string> {
  const out = new Set<string>();
  artifacts
    .filter((artifact) => isWorktableGeometryEntry(artifact.path || artifact.name))
    .forEach((artifact) => {
      const connectorGuidPattern = /ConnectorTemplateGuid[^>]*>\s*([^<]*)/gi;
      for (const match of artifact.text.matchAll(connectorGuidPattern)) {
        const guid = normalizeGuid(match[1] || "");
        if (isRealGuid(guid)) out.add(guid);
      }
    });
  return out;
}

function referencedMeshGuidsFromArtifacts(artifacts: SourceArtifact[]): Set<string> {
  return referencedMeshGuidsFromTexts(
    artifacts
      .filter((artifact) => isWorktableGeometryEntry(artifact.path || artifact.name))
      .map((artifact) => artifact.text)
  );
}

function artifactKindForTextEntry(name: string, text: string): ArtifactKind {
  if (isWorktableMeshEntry(name)) return WORKTABLE_MESH_KIND;
  return detectArtifactKind(name, text);
}


type TextArtifactInput = {
  id: string;
  name: string;
  source: string;
  text: string;
  size: number;
  path: string;
};

type MeshArtifactMetadata = Pick<
  SourceArtifact,
  | "meshGuid"
  | "meshName"
  | "meshSourcePath"
  | "meshPayloadSize"
  | "meshDecodedSize"
  | "unitScaleToMm"
  | "unitScaleSource"
  | "nativeUnit"
  | "modelAssetPath"
  | "modelAssetFormat"
>;

function sourceArtifactForTextEntry(input: TextArtifactInput): SourceArtifact {
  const kind = artifactKindForTextEntry(input.path || input.name, input.text);
  const artifact: SourceArtifact = {
    id: input.id,
    name: input.name,
    kind,
    source: input.source,
    text: input.text,
    size: input.size,
    path: input.path
  };

  if (kind !== WORKTABLE_MESH_KIND) return artifact;
  return {
    ...artifact,
    ...meshArtifactMetadata(input.path || input.name, input.text)
  };
}

function meshArtifactMetadata(pathOrName: string, text: string): MeshArtifactMetadata {
  const guid = normalizeGuid(stemForPath(pathOrName) || xmlTagText(text, "GUID") || xmlTagText(text, "Guid"));
  const name = xmlTagText(text, "ObjectName") || xmlTagText(text, "Name") || guid || archiveDisplayName(pathOrName);
  const payload = meshPayloadText(text);
  const unitScaleToMm = numericXmlTagText(text, "UnitScaleToMm") ?? numericXmlTagText(text, "unitScaleToMm");
  const modelAssetPath = modelAssetPathForMesh(guid, name);

  return {
    meshGuid: guid,
    meshName: name,
    meshSourcePath: pathOrName,
    meshPayloadSize: payload.length,
    meshDecodedSize: decodedBase64Size(payload) ?? undefined,
    unitScaleToMm: unitScaleToMm ?? undefined,
    unitScaleSource: unitScaleToMm ? "xmsh" : undefined,
    nativeUnit: xmlTagText(text, "NativeUnit") || xmlTagText(text, "nativeUnit") || undefined,
    modelAssetPath: modelAssetPath || undefined,
    modelAssetFormat: modelAssetPath ? "glb" : undefined
  };
}

function meshPayloadText(text: string): string {
  return (
    xmlTagText(text, "Mesh") ||
    xmlTagText(text, "Payload") ||
    xmlTagText(text, "Data") ||
    xmlTagText(text, "Binary") ||
    ""
  ).replace(/\s+/g, "");
}

function xmlTagText(text: string, tagName: string): string {
  const escapedName = tagName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = text.match(new RegExp(`<(?:[A-Za-z0-9_.-]+:)?${escapedName}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/(?:[A-Za-z0-9_.-]+:)?${escapedName}>`, "i"));
  return decodeXmlEntities(match?.[1] || "").replace(/\s+/g, " ").trim();
}

function numericXmlTagText(text: string, tagName: string): number | null {
  const value = Number(xmlTagText(text, tagName));
  return Number.isFinite(value) && value > 0 ? value : null;
}

function modelAssetPathForMesh(guid: string, name: string): string {
  const id = isRealGuid(guid) ? guid : modelAssetSlug(name);
  return id ? `/models/fluent/local/${id}.glb` : "";
}

function modelAssetSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "mesh";
}

function decodedBase64Size(value: string): number | null {
  if (!value) return null;
  const normalized = value.replace(/\s+/g, "");
  if (!normalized || !/^[A-Za-z0-9+/]*={0,2}$/.test(normalized)) return null;
  const padding = normalized.endsWith("==") ? 2 : normalized.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((normalized.length * 3) / 4) - padding);
}

function decodeXmlEntities(value: string): string {
  return (value || "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

function isArchivePath(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".zeia") || lower.endsWith(".zip");
}

function isProtocolArchiveTextEntry(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".xscr") || lower.endsWith(".gwl");
}

function isWorktableGeometryEntry(name: string): boolean {
  const extension = extensionForPath(name);
  return extension === ".xwsp" || extension === ".xcmp" || extension === ".xcon" || extension === ".xsit";
}

function isWorktableMeshEntry(name: string): boolean {
  return extensionForPath(name) === ".xmsh";
}

function isRelevantMetadataArchiveTextEntry(name: string): boolean {
  const lower = name.toLowerCase();
  if (isProtocolArchiveTextEntry(lower) || isWorktableGeometryEntry(lower) || isWorktableMeshEntry(lower)) return false;
  if (lower.endsWith(".yaml") || lower.endsWith(".yml")) return lower.includes("alias");
  if (lower.endsWith(".md")) return lower.includes("repair_plan");
  if (!lower.endsWith(".json")) return false;
  return (
    lower.includes("protocol") ||
    lower.includes("simulation") ||
    lower.includes("metadata") ||
    lower.includes("hardware") ||
    lower.includes("worktable") ||
    lower.includes("validation") ||
    lower.includes("repair") ||
    lower.includes("manifest")
  );
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

function isRealGuid(value: string): boolean {
  return Boolean(value && value !== EMPTY_GUID);
}

function normalizeGuid(value: string): string {
  const match = value.match(GUID_IN_TEXT_PATTERN);
  return match?.[0].toLowerCase() || "";
}

function isImagePath(name: string): boolean {
  return IMAGE_EXTENSIONS.has(extensionForPath(name));
}

function stemForPath(name: string): string {
  const cleanName = name.split(/[\\/]/).pop() || name;
  const index = cleanName.lastIndexOf(".");
  return index >= 0 ? cleanName.slice(0, index) : cleanName;
}

function extensionForPath(name: string): string {
  const cleanName = name.split(/[\\/]/).pop() || name;
  const index = cleanName.lastIndexOf(".");
  return index >= 0 ? cleanName.slice(index).toLowerCase() : "";
}

function mimeTypeForPath(name: string): string {
  const extension = extensionForPath(name);
  if (extension === ".jpg" || extension === ".jpeg") return "image/jpeg";
  if (extension === ".png") return "image/png";
  if (extension === ".gif") return "image/gif";
  if (extension === ".webp") return "image/webp";
  return "application/octet-stream";
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("Could not read image"));
    reader.readAsDataURL(blob);
  });
}

function bytesToDataUrl(bytes: Uint8Array, mimeType: string): string {
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return `data:${mimeType};base64,${btoa(binary)}`;
}

function archiveDisplayName(name: string): string {
  const parts = name.split(/[\\/]/).filter(Boolean);
  return parts.slice(-3).join("/");
}