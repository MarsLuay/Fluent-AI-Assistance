import type { ProtocolModel } from "../types";

export type DiagnosticLogMessage = {
  level: "error" | "warn" | "info";
  category: string;
  text: string;
};

export type MeshDiagnosticInput = {
  error: string;
  itemCount: number;
  status: string;
  missingItems: Array<{ guid: string; name: string; assetPath: string }>;
};

export type GeometryDiagnosticInput = {
  fallbackCommandedTargets: string[];
  exactCommandedWellTargets: number;
  commandedWellTargets: number;
  exactTransformedWellLabware: number;
  transformedWellLabware: number;
};

const PASSED_GATE_STATUSES = new Set(["passed", "not_configured", "not_available"]);
const PASSED_ALIAS_STATUSES = new Set(["passed", "applied", "ok", "success", "ready", "skipped"]);

export function collectProtocolDiagnosticMessages(
  model: ProtocolModel,
  mesh: MeshDiagnosticInput,
  geometry: GeometryDiagnosticInput
): DiagnosticLogMessage[] {
  const messages: DiagnosticLogMessage[] = [];

  for (const gate of model.repairs.validationGates) {
    if (PASSED_GATE_STATUSES.has(gate.status)) continue;
    const level = gate.severity === "blocking" || gate.status === "failed" ? "error" : "warn";
    const summary = gate.summary || gate.status.replace(/_/g, " ");
    messages.push({
      level,
      category: "validation-gate",
      text: `Gate ${gate.gate} ${gate.name}: ${summary}`
    });
  }

  const worktableIssues = [...model.repairs.issues, ...model.repairs.validationChecks];
  for (const issue of worktableIssues) {
    if (issue.severity !== "blocking" && issue.severity !== "needs_review") continue;
    const level = issue.severity === "blocking" ? "error" : "warn";
    const detail = issue.detail || issue.suggestion || issue.status;
    const transition =
      issue.sourceValue || issue.targetValue
        ? ` (${issue.sourceValue || "source unknown"} -> ${issue.targetValue || "target unknown"})`
        : "";
    messages.push({
      level,
      category: "worktable-diff",
      text: `${issue.label || issue.kind}: ${detail}${transition}`
    });
  }

  if (model.repairs.summary.blocking > 0 || model.repairs.summary.needsReview > 0) {
    messages.push({
      level: model.repairs.summary.blocking > 0 ? "error" : "warn",
      category: "worktable-diff",
      text: `Worktable diff summary: ${model.repairs.summary.blocking} blocking, ${model.repairs.summary.needsReview} needs review`
    });
  }

  for (const repair of model.repairs.aliasRepairs) {
    const status = repair.status.toLowerCase();
    if (PASSED_ALIAS_STATUSES.has(status)) continue;
    const level = /fail|block|error|missing/.test(status) ? "error" : "warn";
    const rename = repair.newName ? ` (${repair.oldName || repair.kind} -> ${repair.newName})` : "";
    messages.push({
      level,
      category: "alias-repair",
      text: `Alias repair ${repair.status}: ${repair.summary || repair.kind}${rename}`
    });
  }

  if (model.hardware.missingAssets.length > 0) {
    messages.push({
      level: "warn",
      category: "zeia-context",
      text: `${model.hardware.missingAssets.length} hardware asset reference${model.hardware.missingAssets.length === 1 ? "" : "s"} need manual verification`
    });
    for (const asset of model.hardware.missingAssets.slice(0, 12)) {
      messages.push({ level: "warn", category: "zeia-context", text: `Missing hardware asset: ${asset}` });
    }
    if (model.hardware.missingAssets.length > 12) {
      messages.push({
        level: "info",
        category: "zeia-context",
        text: `+ ${model.hardware.missingAssets.length - 12} more missing hardware asset references`
      });
    }
  }

  const reviewAssets = model.hardware.assetDetails.filter((asset) => asset.manualVerificationRequired);
  if (reviewAssets.length > 0) {
    messages.push({
      level: "warn",
      category: "zeia-context",
      text: `${reviewAssets.length} hardware asset${reviewAssets.length === 1 ? "" : "s"} flagged for manual verification`
    });
  }

  for (const note of model.hardware.notes) {
    if (!note.trim()) continue;
    const level = /fail|error|missing|not found|unresolved|invalid/i.test(note) ? "warn" : "info";
    messages.push({ level, category: "zeia-context", text: note });
  }

  if (mesh.error) {
    messages.push({ level: "error", category: "mesh-inventory", text: mesh.error });
  }

  if (mesh.status === "complete" && mesh.itemCount === 0) {
    messages.push({
      level: "info",
      category: "mesh-inventory",
      text: "No Worktable/Meshes/*.xmsh entries loaded"
    });
  }

  for (const missing of mesh.missingItems.slice(0, 16)) {
    messages.push({
      level: "warn",
      category: "mesh-inventory",
      text: `Missing converted model: ${missing.name || missing.guid || "Unnamed mesh"} (${missing.assetPath})`
    });
  }
  if (mesh.missingItems.length > 16) {
    messages.push({
      level: "info",
      category: "mesh-inventory",
      text: `+ ${mesh.missingItems.length - 16} more missing converted model${mesh.missingItems.length - 16 === 1 ? "" : "s"}`
    });
  }

  if (geometry.fallbackCommandedTargets.length > 0) {
    const preview = geometry.fallbackCommandedTargets.slice(0, 6).join(", ");
    const hidden = Math.max(0, geometry.fallbackCommandedTargets.length - 6);
    messages.push({
      level: "warn",
      category: "mesh-inventory",
      text: `Well geometry fallback targets: ${preview}${hidden ? ` + ${hidden} more` : ""}`
    });
  }

  if (
    geometry.commandedWellTargets > 0 &&
    geometry.exactCommandedWellTargets < geometry.commandedWellTargets
  ) {
    messages.push({
      level: "warn",
      category: "mesh-inventory",
      text: `Exact commanded well targets: ${geometry.exactCommandedWellTargets}/${geometry.commandedWellTargets}`
    });
  }

  if (
    geometry.transformedWellLabware > 0 &&
    geometry.exactTransformedWellLabware < geometry.transformedWellLabware
  ) {
    messages.push({
      level: "warn",
      category: "mesh-inventory",
      text: `Exact transformed well labware: ${geometry.exactTransformedWellLabware}/${geometry.transformedWellLabware}`
    });
  }

  if (!model.sourceSummaries.length && model.commands.length > 0) {
    messages.push({
      level: "warn",
      category: "source-artifacts",
      text: "No source artifact summaries loaded for the active protocol"
    });
  }

  return messages;
}

export function diagnosticFingerprint(messages: DiagnosticLogMessage[]): string {
  return messages.map((message) => `${message.level}|${message.category}|${message.text}`).join("\n");
}

export function emitProtocolDiagnostics(messages: DiagnosticLogMessage[]): void {
  if (!messages.length) return;

  const errors = messages.filter((message) => message.level === "error").length;
  const warnings = messages.filter((message) => message.level === "warn").length;
  console.info(
    `[diagnostics] ${messages.length} item(s): ${errors} error${errors === 1 ? "" : "s"}, ${warnings} warning${warnings === 1 ? "" : "s"}`
  );

  for (const message of messages) {
    const line = `[${message.category}] ${message.text}`;
    if (message.level === "error") console.error(line);
    else if (message.level === "warn") console.warn(line);
    else console.info(line);
  }
}
