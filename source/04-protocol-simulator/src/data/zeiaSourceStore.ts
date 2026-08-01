import type { SourceArtifact } from "../types";

export const IMPORT_ZEIA_OPTION_ID = "__import_zeia__";
/** @deprecated Use IMPORT_ZEIA_OPTION_ID */
export const IMPORT_MORE_ZEIA_OPTION_ID = IMPORT_ZEIA_OPTION_ID;
export const SOURCE_SELECT_PLACEHOLDER = "";
export const ZEIA_SAMPLE_PREFIX = "sample:";
export const ZEIA_IMPORT_PREFIX = "import:";

const IMPORTS_STORAGE_KEY = "tecan-simulator.zeia-imports.v1";
const ACTIVE_SOURCE_STORAGE_KEY = "tecan-simulator.active-zeia-source.v1";

export type SavedZeiaImport = {
  id: string;
  label: string;
  importedAt: string;
  artifacts: SourceArtifact[];
};

export function zeiaOptionIdForSample(sampleId: string): string {
  return `${ZEIA_SAMPLE_PREFIX}${sampleId}`;
}

export function zeiaOptionIdForImport(importId: string): string {
  return `${ZEIA_IMPORT_PREFIX}${importId}`;
}

export function parseZeiaOptionId(optionId: string): { kind: "sample" | "import"; id: string } | null {
  if (optionId.startsWith(ZEIA_SAMPLE_PREFIX)) {
    return { kind: "sample", id: optionId.slice(ZEIA_SAMPLE_PREFIX.length) };
  }
  if (optionId.startsWith(ZEIA_IMPORT_PREFIX)) {
    return { kind: "import", id: optionId.slice(ZEIA_IMPORT_PREFIX.length) };
  }
  return null;
}

export function loadSavedZeiaImports(): SavedZeiaImport[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(IMPORTS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isValidSavedZeiaImport);
  } catch {
    return [];
  }
}

export function saveSavedZeiaImports(imports: SavedZeiaImport[]): SavedZeiaImport[] {
  if (typeof window === "undefined") return imports;
  const sorted = [...imports].sort((left, right) => right.importedAt.localeCompare(left.importedAt));
  let next = sorted;
  while (next.length) {
    try {
      window.localStorage.setItem(IMPORTS_STORAGE_KEY, JSON.stringify(next));
      return next;
    } catch (error) {
      if (!isQuotaExceededError(error)) throw error;
      next = next.slice(0, Math.max(1, next.length - 1));
    }
  }
  try {
    window.localStorage.removeItem(IMPORTS_STORAGE_KEY);
  } catch {
    // Ignore storage cleanup failures.
  }
  return [];
}

export function loadActiveZeiaSourceKey(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(ACTIVE_SOURCE_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function saveActiveZeiaSourceKey(optionId: string): void {
  if (typeof window === "undefined") return;
  try {
    if (optionId) window.localStorage.setItem(ACTIVE_SOURCE_STORAGE_KEY, optionId);
    else window.localStorage.removeItem(ACTIVE_SOURCE_STORAGE_KEY);
  } catch {
    // Ignore persistence failures.
  }
}

export function pruneSavedZeiaImports(imports: SavedZeiaImport[]): SavedZeiaImport[] {
  return imports.filter((entry) => isValidSavedZeiaImport(entry));
}

export function resolveActiveZeiaSourceKey(
  preferredKey: string,
  sampleIds: string[],
  imports: SavedZeiaImport[]
): string {
  const importIds = new Set(imports.map((entry) => entry.id));
  const sampleIdSet = new Set(sampleIds);

  const candidates = [preferredKey, loadActiveZeiaSourceKey()].filter(Boolean);
  for (const candidate of candidates) {
    if (!candidate || candidate === IMPORT_ZEIA_OPTION_ID) continue;
    const parsed = parseZeiaOptionId(candidate);
    if (!parsed) continue;
    if (parsed.kind === "sample" && sampleIdSet.has(parsed.id)) return candidate;
    if (parsed.kind === "import" && importIds.has(parsed.id)) return candidate;
  }
  return "";
}

const LAUNCH_BUNDLE_SAMPLE_ID = "launch-bundle";

/** Pick the startup ZEIA source: URL override, then last-used, then launch bundle, then default sample. */
export function resolveStartupZeiaSourceKey(options: {
  requestedSampleId?: string;
  sampleIds: string[];
  imports: SavedZeiaImport[];
  hasLaunchBundle?: boolean;
  defaultSampleId?: string;
}): string {
  const { requestedSampleId, sampleIds, imports, hasLaunchBundle = false, defaultSampleId } = options;
  const preferenceOrder: string[] = [];

  if (requestedSampleId) preferenceOrder.push(zeiaOptionIdForSample(requestedSampleId));

  const savedKey = loadActiveZeiaSourceKey();
  if (savedKey && !preferenceOrder.includes(savedKey)) preferenceOrder.push(savedKey);

  if (hasLaunchBundle) preferenceOrder.push(zeiaOptionIdForSample(LAUNCH_BUNDLE_SAMPLE_ID));

  if (defaultSampleId) {
    const defaultKey = zeiaOptionIdForSample(defaultSampleId);
    if (!preferenceOrder.includes(defaultKey)) preferenceOrder.push(defaultKey);
  }

  for (const candidate of preferenceOrder) {
    const resolved = resolveSingleZeiaSourceKey(candidate, sampleIds, imports);
    if (resolved) return resolved;
  }

  return "";
}

function resolveSingleZeiaSourceKey(
  candidate: string,
  sampleIds: string[],
  imports: SavedZeiaImport[]
): string {
  if (!candidate || candidate === IMPORT_ZEIA_OPTION_ID) return "";
  const importIds = new Set(imports.map((entry) => entry.id));
  const sampleIdSet = new Set(sampleIds);
  const parsed = parseZeiaOptionId(candidate);
  if (!parsed) return "";
  if (parsed.kind === "sample" && sampleIdSet.has(parsed.id)) return candidate;
  if (parsed.kind === "import" && importIds.has(parsed.id)) return candidate;
  return "";
}

export function labelForImportedArtifacts(artifacts: SourceArtifact[], fileNames: string[] = []): string {
  const zeiaArtifact = artifacts.find((artifact) => artifact.kind === "zeia" || /\.zeia$/i.test(artifact.name || artifact.path || ""));
  if (zeiaArtifact?.name) return sanitizeImportLabel(zeiaArtifact.name);
  const zeiaFile = fileNames.find((name) => /\.zeia$/i.test(name));
  if (zeiaFile) return sanitizeImportLabel(zeiaFile);
  const archiveFile = fileNames.find((name) => /\.(zeia|zip)$/i.test(name));
  if (archiveFile) return sanitizeImportLabel(archiveFile);
  if (fileNames[0]) return sanitizeImportLabel(fileNames[0]);
  if (artifacts[0]?.name) return sanitizeImportLabel(artifacts[0].name);
  return "Imported ZEIA";
}

export function createSavedZeiaImport(artifacts: SourceArtifact[], fileNames: string[] = []): SavedZeiaImport {
  return {
    id: typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `import-${Date.now()}`,
    label: labelForImportedArtifacts(artifacts, fileNames),
    importedAt: new Date().toISOString(),
    artifacts
  };
}

function isValidSavedZeiaImport(entry: unknown): entry is SavedZeiaImport {
  if (!entry || typeof entry !== "object") return false;
  const record = entry as SavedZeiaImport;
  return Boolean(
    typeof record.id === "string" &&
      record.id &&
      typeof record.label === "string" &&
      record.label &&
      typeof record.importedAt === "string" &&
      Array.isArray(record.artifacts) &&
      record.artifacts.length > 0 &&
      record.artifacts.every(
        (artifact) =>
          artifact &&
          typeof artifact === "object" &&
          typeof artifact.id === "string" &&
          typeof artifact.name === "string" &&
          typeof artifact.kind === "string" &&
          typeof artifact.source === "string" &&
          typeof artifact.text === "string"
      )
  );
}

function sanitizeImportLabel(value: string): string {
  const base = value.split(/[\\/]/).pop() || value;
  return base.replace(/\.(zeia|zip)$/i, "") || base;
}

function isQuotaExceededError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const record = error as { name?: string; code?: number };
  return record.name === "QuotaExceededError" || record.code === 22;
}
