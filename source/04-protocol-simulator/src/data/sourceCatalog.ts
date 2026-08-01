import type { ProtocolModel, SampleDataset, SourceArtifact } from "../types";
import { IMPORT_ZEIA_OPTION_ID, type SavedZeiaImport, zeiaOptionIdForImport, zeiaOptionIdForSample } from "./zeiaSourceStore";

export const LAUNCH_BUNDLE_SAMPLE_ID = "launch-bundle";

const SCRIPT_ARTIFACT_KINDS = new Set(["xscr", "protocol-ir", "gwl", "simulation"]);

export type SourceSelectorOption = {
  id: string;
  label: string;
  detail?: string;
};

export type SourceSelectionHints = {
  scriptArtifactKey?: string;
  scriptPath?: string;
  scriptName?: string;
  worktableName?: string;
};

export function artifactSelectionKey(artifact: SourceArtifact): string {
  return artifact.id || artifact.path || artifact.name;
}

export function collectZeiaOptions(samples: SampleDataset[], savedImports: SavedZeiaImport[]): SourceSelectorOption[] {
  const options: SourceSelectorOption[] = [];

  samples.forEach((sample) => {
    options.push({
      id: zeiaOptionIdForSample(sample.id),
      label: sample.name,
      detail: sample.description
    });
  });

  savedImports.forEach((entry) => {
    options.push({
      id: zeiaOptionIdForImport(entry.id),
      label: entry.label,
      detail: "Imported ZEIA"
    });
  });

  options.push({
    id: IMPORT_ZEIA_OPTION_ID,
    label: "Import ZEIA",
    detail: "Browse for a ZEIA archive"
  });

  return options;
}

export function collectScriptOptions(artifacts: SourceArtifact[]): SourceSelectorOption[] {
  return artifacts
    .filter((artifact) => SCRIPT_ARTIFACT_KINDS.has(artifact.kind))
    .map((artifact) => ({
      id: artifactSelectionKey(artifact),
      label: humanArtifactLabel(artifact),
      detail: artifact.kind.toUpperCase()
    }));
}

export function collectWorktableOptions(model: ProtocolModel, hasLoadedSource = true): SourceSelectorOption[] {
  if (!hasLoadedSource) return [];

  const seen = new Set<string>();
  const options: SourceSelectorOption[] = [];

  const add = (value: string, detail = "worktable") => {
    const label = sanitizeSelectorLabel(value);
    if (!label || seen.has(label)) return;
    seen.add(label);
    options.push({ id: label, label, detail });
  };

  add(model.worktableName, "active");
  add(model.repairs.protocolWorktable, "protocol");
  add(model.repairs.sourceWorktable, "source");

  if (!options.length) {
    return [];
  }

  return options;
}

export function sourceSelectionHintsFromArtifacts(artifacts: SourceArtifact[]): SourceSelectionHints {
  const hints: SourceSelectionHints = {};

  for (const artifact of artifacts) {
    if (!artifact.text.trim() || !/\.json$/i.test(artifact.name || artifact.path || "")) continue;
    try {
      const payload = JSON.parse(artifact.text) as Record<string, unknown>;
      if (/metadata\.json$/i.test(artifact.name || artifact.path || "")) {
        mergeHints(hints, hintsFromBundleMetadata(payload));
        continue;
      }
      if (/generation_manifest\.json$/i.test(artifact.name || artifact.path || "")) {
        mergeHints(hints, hintsFromGenerationManifest(payload));
        continue;
      }
      if (artifact.kind === "protocol-ir" || payload.ir_version || (Array.isArray(payload.steps) && Array.isArray(payload.labware))) {
        mergeHints(hints, hintsFromProtocolIr(payload));
      }
    } catch {
      // Ignore unrelated JSON artifacts.
    }
  }

  return hints;
}

export function resolvePreferredScriptArtifactKey(artifacts: SourceArtifact[]): string {
  const scriptOptions = collectScriptOptions(artifacts);
  if (!scriptOptions.length) return "";

  const scriptArtifacts = artifacts.filter((artifact) => SCRIPT_ARTIFACT_KINDS.has(artifact.kind));
  const hints = sourceSelectionHintsFromArtifacts(artifacts);

  if (hints.scriptArtifactKey && scriptOptions.some((option) => option.id === hints.scriptArtifactKey)) {
    return hints.scriptArtifactKey;
  }

  const hintedArtifact = scriptArtifacts.find(
    (artifact) =>
      (hints.scriptPath ? artifactMatchesPathHint(artifact, hints.scriptPath) : false) ||
      (hints.scriptName ? artifactMatchesNameHint(artifact, hints.scriptName) : false)
  );
  if (hintedArtifact) return artifactSelectionKey(hintedArtifact);

  const ranked = [
    (artifact: SourceArtifact) => artifact.kind === "protocol-ir" && !isAuxiliaryScriptArtifact(artifact),
    (artifact: SourceArtifact) => artifact.kind === "xscr" && /generated/i.test(`${artifact.path || ""} ${artifact.name || ""}`),
    (artifact: SourceArtifact) => artifact.kind === "xscr" && !isAuxiliaryScriptArtifact(artifact),
    (artifact: SourceArtifact) => artifact.kind === "simulation",
    (artifact: SourceArtifact) => artifact.kind === "gwl"
  ];

  for (const rank of ranked) {
    const match = scriptArtifacts.find((artifact) => rank(artifact));
    if (match) return artifactSelectionKey(match);
  }

  return scriptOptions[0].id;
}

export function resolvePreferredWorktableName(artifacts: SourceArtifact[], model: ProtocolModel): string {
  const options = collectWorktableOptions(model, artifacts.length > 0);
  if (!options.length) return "";

  const hints = sourceSelectionHintsFromArtifacts(artifacts);
  const candidates = [
    hints.worktableName,
    model.worktableName,
    model.repairs.protocolWorktable,
    model.repairs.sourceWorktable
  ];

  for (const candidate of candidates) {
    const label = sanitizeSelectorLabel(candidate || "");
    if (label && options.some((option) => option.id === label)) return label;
  }

  return options[0].id;
}

function mergeHints(target: SourceSelectionHints, next: SourceSelectionHints): void {
  if (next.scriptArtifactKey) target.scriptArtifactKey = next.scriptArtifactKey;
  if (next.scriptPath) target.scriptPath = next.scriptPath;
  if (next.scriptName) target.scriptName = next.scriptName;
  if (next.worktableName) target.worktableName = next.worktableName;
}

function hintsFromBundleMetadata(payload: Record<string, unknown>): SourceSelectionHints {
  const hints: SourceSelectionHints = {};
  const compiledXscr = stringField(payload.compiled_xscr);
  if (compiledXscr) {
    hints.scriptPath = compiledXscr;
    hints.scriptName = basenamePath(compiledXscr);
  }

  const files = Array.isArray(payload.files) ? payload.files : [];
  for (const entry of files) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const kind = stringField(record.kind);
    if (kind === "protocol-ir") {
      const relativePath = stringField(record.relative_path);
      const filename = stringField(record.filename);
      if (!hints.scriptPath && relativePath) hints.scriptPath = relativePath;
      if (!hints.scriptName && filename) hints.scriptName = basenamePath(filename);
    }
  }

  return hints;
}

function hintsFromGenerationManifest(payload: Record<string, unknown>): SourceSelectionHints {
  const hints: SourceSelectionHints = {};
  const compiledXscr = stringField(payload.compiled_xscr);
  if (compiledXscr) {
    hints.scriptPath = compiledXscr;
    hints.scriptName = basenamePath(compiledXscr);
  }
  return hints;
}

function hintsFromProtocolIr(payload: Record<string, unknown>): SourceSelectionHints {
  const hints: SourceSelectionHints = {};
  const worktable = payload.worktable;
  if (worktable && typeof worktable === "object") {
    const name = stringField((worktable as Record<string, unknown>).name);
    if (name) hints.worktableName = name;
  }

  const dependencies = Array.isArray(payload.dependencies) ? payload.dependencies : [];
  for (const dependency of dependencies) {
    if (!dependency || typeof dependency !== "object") continue;
    const record = dependency as Record<string, unknown>;
    const kind = stringField(record.kind);
    if (kind === "WorktableWorkspace" || kind === "worktable") {
      const name = stringField(record.name);
      if (name) hints.worktableName = name;
      break;
    }
  }

  const protocolName = stringField(payload.name) || stringField(payload.id);
  if (protocolName) hints.scriptName = protocolName;

  return hints;
}

function artifactMatchesPathHint(artifact: SourceArtifact, hintPath: string): boolean {
  const normalizedHint = normalizeComparablePath(hintPath);
  const artifactPath = normalizeComparablePath(artifact.path || artifact.name || "");
  if (!normalizedHint || !artifactPath) return false;
  return artifactPath === normalizedHint || artifactPath.endsWith(normalizedHint) || normalizedHint.endsWith(artifactPath);
}

function artifactMatchesNameHint(artifact: SourceArtifact, hintName: string): boolean {
  const normalizedHint = normalizeComparableName(hintName);
  if (!normalizedHint) return false;
  const candidates = [artifact.name, artifact.path, humanArtifactLabel(artifact)].map((value) => normalizeComparableName(value || ""));
  return candidates.some((candidate) => candidate === normalizedHint || candidate.includes(normalizedHint) || normalizedHint.includes(candidate));
}

function isAuxiliaryScriptArtifact(artifact: SourceArtifact): boolean {
  const label = `${artifact.path || ""} ${artifact.name || ""}`.toLowerCase();
  return /subroutine/.test(label) || /[/\\]sub_/.test(label) || /source[_-]script/.test(label) || /original-sources/.test(label);
}

function normalizeComparablePath(value: string): string {
  return value.replace(/\\/g, "/").toLowerCase().replace(/^\.\//, "");
}

function normalizeComparableName(value: string): string {
  return basenamePath(value).replace(/\.(xscr|gwl|json|py)$/i, "").toLowerCase();
}

function basenamePath(value: string): string {
  const normalized = value.replace(/\\/g, "/");
  return normalized.split("/").pop() || normalized;
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function humanArtifactLabel(artifact: SourceArtifact): string {
  const raw = artifact.name || artifact.path || artifact.kind;
  const base = raw.split(/[\\/]/).pop() || raw;
  return base.replace(/\.(xscr|gwl|json|zeia|py)$/i, "") || base;
}

function sanitizeSelectorLabel(value: string): string {
  const trimmed = value.replace(/\s+/g, " ").trim();
  if (!trimmed) return "";
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(trimmed)) return "";
  if (/[\\/]/.test(trimmed) || /\.(xscr|gwl|json|zeia|py)$/i.test(trimmed)) {
    const base = trimmed.split(/[\\/]/).pop() || trimmed;
    return base.replace(/\.(xscr|gwl|json|zeia|py)$/i, "") || "";
  }
  return trimmed;
}
