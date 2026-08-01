import type { ProtocolReadiness, ValidationGate, WorktableIssue, WorktableIssueKind } from "../types";
import { READINESS_GATE_DEFINITIONS } from "./readinessGateRegistry";

export function readinessFromRecord(record: Record<string, unknown>): ProtocolReadiness | undefined {
  const readiness = objectAt(record, "readiness");
  const keys = [
    "offline_validation",
    "review_state",
    "fluentcontrol_load_diagnostic",
    "generated_zeia_import",
    "script_editor_load",
    "simulation",
    "hardware_run"
  ] as const;

  if (!keys.every((key) => stringAt(objectAt(readiness, key), "status"))) return undefined;

  return {
    offline_validation: objectAt(readiness, "offline_validation") as ProtocolReadiness["offline_validation"],
    review_state: objectAt(readiness, "review_state") as ProtocolReadiness["review_state"],
    fluentcontrol_load_diagnostic: objectAt(
      readiness,
      "fluentcontrol_load_diagnostic"
    ) as ProtocolReadiness["fluentcontrol_load_diagnostic"],
    generated_zeia_import: objectAt(readiness, "generated_zeia_import") as ProtocolReadiness["generated_zeia_import"],
    script_editor_load: objectAt(readiness, "script_editor_load") as ProtocolReadiness["script_editor_load"],
    simulation: objectAt(readiness, "simulation") as ProtocolReadiness["simulation"],
    hardware_run: objectAt(readiness, "hardware_run") as ProtocolReadiness["hardware_run"]
  };
}

export function validationGatesFromReadyReport(report: Record<string, unknown>, source: string): ValidationGate[] {
  return arrayAt(report, "gates").map((gate, index) => validationGateFromRecord(gate, source, index));
}

export function validationGatesFromRequestDiff(diff: Record<string, unknown>, source: string): ValidationGate[] {
  return arrayAt(diff, "checks")
    .filter((check) => stringAt(check, "id") === "ready_validation")
    .flatMap((check) => arrayAt(objectAt(check, "details"), "failed_gates"))
    .map((gate, index) => validationGateFromRecord(gate, source, index));
}

export function validationGatesFromMarkdown(text: string, source: string): ValidationGate[] {
  const gateMatches = Array.from(text.matchAll(/^Gate\s+(\d+)\.\s+(.+)$/gm));
  return gateMatches.map((match, index) => {
    const blockStart = (match.index ?? 0) + match[0].length;
    const blockEnd = gateMatches[index + 1]?.index ?? text.length;
    const block = text.slice(blockStart, blockEnd);
    const gateNumber = Number(match[1]);
    const definition = READINESS_GATE_DEFINITIONS.find((item) => item.gateNumber === gateNumber);
    const status = markdownField(block, "Status") || "unknown";
    const summary = markdownField(block, "Summary");
    const details = detailsFromMarkdownBlock(block);
    return {
      id: definition?.id || slug(`gate-${gateNumber}-${match[2]}`),
      gate: definition?.gate || `Gate ${gateNumber}`,
      gateNumber,
      name: match[2].trim(),
      status,
      severity: statusSeverity(status),
      summary,
      details,
      source
    };
  });
}

export function completeValidationGates(gates: ValidationGate[], source: string): ValidationGate[] {
  if (!gates.length) return [];
  const byNumber = new Map(gates.map((gate) => [gate.gateNumber, gate]));
  return READINESS_GATE_DEFINITIONS.map((definition) => {
    const gate = byNumber.get(definition.gateNumber);
    if (gate) return gate;
    return {
      id: definition.id,
      gate: definition.gate,
      gateNumber: definition.gateNumber,
      name: definition.name,
      status: definition.classification === "optional_diagnostic" ? "not_configured" : "not_available",
      severity: "info",
      summary:
        definition.classification === "optional_diagnostic"
          ? "Optional FluentControl import/load diagnostic was not configured for this validation run."
          : "This gate was not present in the loaded validation artifact.",
      details: {},
      source
    };
  });
}

export function validationGateFromRecord(gate: Record<string, unknown>, source: string, index: number): ValidationGate {
  const gateNumber = gateNumberFromRecord(gate, index);
  const definition = READINESS_GATE_DEFINITIONS.find((item) => item.gateNumber === gateNumber);
  const status = stringAt(gate, "status") || "unknown";
  return {
    id: stringAt(gate, "id") || definition?.id || `gate-${gateNumber || index + 1}`,
    gate: stringAt(gate, "gate") || definition?.gate || `Gate ${gateNumber || index + 1}`,
    gateNumber: gateNumber || index + 1,
    name: stringAt(gate, "name") || definition?.name || "Validation gate",
    status,
    severity: statusSeverity(status),
    summary: stringAt(gate, "summary"),
    details: objectAt(gate, "details"),
    source
  };
}

export function issueFromValidationGate(gate: ValidationGate): WorktableIssue {
  return {
    id: `validation-gate-${gate.id}`,
    kind: validationIssueKind(gate.id),
    severity: gate.severity,
    status: gate.status,
    label: `${gate.gate}: ${gate.name}`,
    detail: gate.summary,
    sourceValue: "",
    targetValue: "",
    suggestion: gate.status === "passed" ? "No action needed." : suggestionForGate(gate),
    diffPath: `/gates/${Math.max(0, gate.gateNumber - 1)}`
  };
}

function suggestionForGate(gate: ValidationGate): string {
  const lower = `${gate.id} ${gate.name}`.toLowerCase();
  if (lower.includes("labware")) return "Open the linked labware/site target and confirm aliases or worktable placement.";
  if (lower.includes("liquid") || lower.includes("volume") || lower.includes("tip")) return "Open the linked liquid movement and review volume, tip, and liquid-class evidence.";
  if (lower.includes("xscr") || lower.includes("recreate") || lower.includes("raw_xml")) return "Open the linked command or source command evidence before importing.";
  if (lower.includes("context")) return "Run the FluentControl context check in simulation mode on the target system.";
  return "Review the linked command, labware, or gate details before import.";
}

function gateNumberFromRecord(gate: Record<string, unknown>, fallbackIndex: number): number {
  const explicit = numberAt(gate, "gate_number") ?? numberAt(gate, "gateNumber");
  if (explicit !== null) return explicit;
  const gateLabel = stringAt(gate, "gate");
  const match = gateLabel.match(/(\d+)/);
  return match ? Number(match[1]) : fallbackIndex + 1;
}

function markdownField(block: string, field: string): string {
  const match = block.match(new RegExp(`^\\s*-\\s+${field}:\\s+(.*)$`, "im"));
  if (!match) return "";
  return cleanMarkdownValue(match[1]);
}

function detailsFromMarkdownBlock(block: string): Record<string, unknown> {
  const details: Record<string, unknown> = {};
  Array.from(block.matchAll(/^\s*-\s+([^:]+):\s+(.*)$/gm)).forEach((match) => {
    const key = match[1].trim();
    if (key === "Status" || key === "Summary") return;
    details[slug(key).replace(/-/g, "_")] = parseMarkdownDetailValue(cleanMarkdownValue(match[2]));
  });
  return details;
}

function cleanMarkdownValue(value: string): string {
  return value.trim().replace(/^`/, "").replace(/`$/, "");
}

function parseMarkdownDetailValue(value: string): unknown {
  if (!value) return "";
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  if ((value.startsWith("[") && value.endsWith("]")) || (value.startsWith("{") && value.endsWith("}"))) {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  return value;
}

function validationIssueKind(id: string): WorktableIssueKind {
  const lower = id.toLowerCase();
  if (lower.includes("worklist")) return "worklist";
  if (lower.includes("liquid")) return "liquid_class";
  if (lower.includes("repair")) return "warning";
  if (lower.includes("worktable")) return "worktable";
  return "validation";
}

function statusSeverity(status: string): WorktableIssue["severity"] {
  const lower = status.toLowerCase();
  if (lower.includes("block") || lower.includes("missing") || lower.includes("failed")) return "blocking";
  if (lower.includes("review") || lower.includes("user") || lower.includes("warning") || lower.includes("changed")) return "needs_review";
  if (lower.includes("safe") || lower.includes("available") || lower.includes("passed") || lower.includes("ready")) return "safe";
  return "info";
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "item";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function objectAt(value: Record<string, unknown>, key: string): Record<string, unknown> {
  return asRecord(value[key]);
}

function arrayAt(value: Record<string, unknown> | unknown, key: string): Record<string, unknown>[] {
  const record = asRecord(value);
  const item = record[key];
  return Array.isArray(item) ? item.filter((entry) => typeof entry === "object").map(asRecord) : [];
}

function stringAt(value: unknown, key: string): string {
  const item = asRecord(value)[key];
  return typeof item === "string" || typeof item === "number" || typeof item === "boolean" ? String(item) : "";
}

function numberAt(value: unknown, key: string): number | null {
  const item = asRecord(value)[key];
  const parsed = typeof item === "number" ? item : typeof item === "string" ? Number(item) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
}
