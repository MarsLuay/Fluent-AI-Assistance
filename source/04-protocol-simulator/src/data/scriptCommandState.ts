import type { OperationFamily, ScriptEditorCommand, ScriptValidationIssue } from "../types";
import { isProtocolIrOperation, protocolIrOperationValidationMessage } from "./protocolIrOperations";

export const EDITOR_OPERATION_FAMILIES: readonly OperationFamily[] = [
  "setup",
  "labware",
  "tips",
  "liquid",
  "motion",
  "prompt",
  "flow",
  "wash",
  "comment",
  "opaque"
];

export function familyForEditorOperation(operation: string): OperationFamily {
  const lower = operation.toLowerCase();
  if (lower.includes("aspirate") || lower.includes("dispense") || lower.includes("mix")) return "liquid";
  if (lower.includes("tip") || lower.includes("adapter")) return "tips";
  if (lower.includes("move") || lower.includes("transfer")) return "motion";
  if (lower.includes("wash")) return "wash";
  if (lower.includes("prompt") || lower.includes("user")) return "prompt";
  if (lower.includes("loop") || lower.includes("if") || lower.includes("branch")) return "flow";
  if (lower.includes("labware")) return "labware";
  if (lower.includes("comment")) return "comment";
  return "opaque";
}

export function scriptEditorCommandFromRecord(record: Record<string, unknown>): ScriptEditorCommand {
  const operation = typeof record.operation === "string" ? record.operation.trim() : "";
  const registeredOperation = isProtocolIrOperation(operation);
  const restoredFamily = registeredOperation && EDITOR_OPERATION_FAMILIES.includes(record.family as OperationFamily)
    ? (record.family as OperationFamily)
    : registeredOperation
      ? familyForEditorOperation(operation)
      : "opaque";
  const validationIssues = scriptValidationIssuesFromUnknown(record.validationIssues);
  const validationMessages =
    validationIssues.length > 0
      ? validationIssues.map((issue) => issue.message)
      : Array.isArray(record.validationMessages)
        ? record.validationMessages.map((message) => String(message)).filter(Boolean)
        : [];
  const operationIssue = protocolIrOperationValidationMessage(operation);
  if (operationIssue && !validationMessages.includes(operationIssue)) validationMessages.push(operationIssue);
  if (operationIssue && !validationIssues.some((issue) => issue.message === operationIssue)) {
    validationIssues.push({ field: "general", message: operationIssue });
  }

  return {
    id: String(record.id || "restored-command"),
    sourceCommandId: typeof record.sourceCommandId === "string" ? record.sourceCommandId : undefined,
    name: String(record.name || "Restored Command"),
    operation,
    family: restoredFamily,
    targetLabware: String(record.targetLabware || ""),
    wells: Array.isArray(record.wells) ? record.wells.map((well) => String(well)).filter(Boolean) : [],
    volumeUl: typeof record.volumeUl === "number" && Number.isFinite(record.volumeUl) ? record.volumeUl : null,
    liquidClass: String(record.liquidClass || ""),
    message: String(record.message || ""),
    specs: specsFromRecord(record.specs),
    enabled: typeof record.enabled === "boolean" ? record.enabled : true,
    dirty: typeof record.dirty === "boolean" ? record.dirty : true,
    validationIssues,
    validationMessages
  };
}

function scriptValidationIssuesFromUnknown(value: unknown): ScriptValidationIssue[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => {
      if (!isRecord(entry) || typeof entry.message !== "string") return null;
      return {
        field: typeof entry.field === "string" ? (entry.field as ScriptValidationIssue["field"]) : "general",
        message: entry.message
      };
    })
    .filter((issue): issue is ScriptValidationIssue => Boolean(issue));
}

function specsFromRecord(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).map(([key, specValue]) => [key, String(specValue ?? "")]));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
