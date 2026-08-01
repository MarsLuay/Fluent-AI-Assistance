import assert from "node:assert/strict";
import {
  buildProtocolModel,
  completeValidationGates,
  readinessFromRecord as readinessFromParsers,
  validationGatesFromMarkdown,
  validationGatesFromReadyReport
} from "../../src/data/parsers";
import { readinessFromRecord } from "../../src/data/readinessParser";
import type { ProtocolReadiness, SourceArtifact } from "../../src/types";

const readiness: ProtocolReadiness = {
  offline_validation: {
    status: "ready_to_import",
    summary: "All required offline readiness gates passed."
  },
  review_state: {
    status: "hardware_review_required",
    summary: "Operator review is still required."
  },
  fluentcontrol_load_diagnostic: {
    status: "load_failed",
    summary: "FluentControl import/load diagnostic reported a load failure."
  },
  generated_zeia_import: {
    status: "ready_to_import",
    summary: "Archive import/checksum health only."
  },
  script_editor_load: {
    status: "load_failed",
    summary: "Script Editor load failed."
  },
  simulation: {
    status: "passed",
    summary: "Offline simulation passed."
  },
  hardware_run: {
    status: "hardware_review_required",
    summary: "Hardware review is still required."
  }
};

assert.equal(readinessFromParsers, readinessFromRecord);
assert.deepEqual(readinessFromRecord({ readiness }), readiness);
assert.equal(readinessFromRecord({}), undefined);

const readyReportGates = validationGatesFromReadyReport(
  {
    gates: [
      {
        id: "fluent_context_check",
        gate: "Gate 27",
        name: "FluentControl import/load diagnostic",
        status: "failed",
        summary: "Load failed."
      }
    ]
  },
  "ready_validation.json"
);
assert.equal(readyReportGates[0]?.gateNumber, 27);
assert.equal(readyReportGates[0]?.severity, "blocking");
assert.equal(
  completeValidationGates(readyReportGates, "ready_validation.json").find((gate) => gate.gateNumber === 27)?.status,
  "failed"
);

const markdownGates = validationGatesFromMarkdown(
  "Gate 3. all labware names resolve\n- Status: `passed`\n- Summary: `All aliases resolve.`\n- Matched aliases: 4\n",
  "ready_validation.md"
);
assert.deepEqual(markdownGates[0]?.details, { matched_aliases: 4 });
assert.equal(markdownGates[0]?.status, "passed");

const artifacts: SourceArtifact[] = [
  {
    id: "protocol-ir",
    name: "protocol.ir.json",
    kind: "protocol-ir",
    source: "test",
    path: "source/protocol.ir.json",
    text: JSON.stringify({
      ir_version: "tecan.protocol_ir.v1",
      id: "readiness-test",
      name: "Readiness Test",
      worktable: { name: "WT_Test" },
      steps: [],
      labware: []
    }),
    size: 0
  },
  {
    id: "metadata",
    name: "metadata.json",
    kind: "metadata",
    source: "test",
    path: "source/metadata.json",
    text: JSON.stringify({
      context_name: "readiness-test",
      script_name: "generated_script.xscr",
      readiness_status: "load_failed",
      readiness
    }),
    size: 0
  },
  {
    id: "manifest",
    name: "generation_manifest.json",
    kind: "metadata",
    source: "test",
    path: "source/generation_manifest.json",
    text: JSON.stringify({
      workflow_status: "ready_to_import",
      readiness_status: "load_failed",
      readiness
    }),
    size: 0
  },
  {
    id: "validation-report",
    name: "validation_report.json",
    kind: "validation-diff",
    source: "test",
    path: "source/reports/validation_report.json",
    text: JSON.stringify({
      validation_version: "test",
      readiness_status: "load_failed",
      readiness,
      gates: [
        {
          id: "fluent_context_check",
          gate: "Gate 27",
          name: "FluentControl import/load diagnostic",
          status: "failed",
          summary: "Load failed."
        }
      ]
    }),
    size: 0
  }
];

const model = buildProtocolModel(artifacts);

assert.equal(model.readinessStatus, "load_failed");
assert.deepEqual(model.readiness, readiness);
assert.equal(model.readiness?.script_editor_load.status, "load_failed");
assert.equal(model.readiness?.simulation.status, "passed");
assert.equal(
  model.repairs.validationGates.find((gate) => gate.id === "fluent_context_check")?.status,
  "failed"
);

console.log("readiness parsing test passed");
