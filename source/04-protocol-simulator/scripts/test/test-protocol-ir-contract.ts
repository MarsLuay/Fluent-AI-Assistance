import assert from "node:assert/strict";
import {
  PROTOCOL_IR_OPERATIONS,
  PROTOCOL_IR_SCHEMA_ID,
  PROTOCOL_IR_VERSION,
  isProtocolIrDocument,
  validateProtocolIr
} from "../../src/data/protocolIrContract";
import { buildProtocolModel } from "../../src/data/parsers";
import type { SourceArtifact } from "../../src/types";

const validIr = {
  ir_version: PROTOCOL_IR_VERSION,
  id: "simulator-contract-test",
  protocol: { name: "Simulator contract test" },
  source: { format: "test" },
  worktable: { name: "WT_Test" },
  labware: [],
  reagents: [],
  liquid_classes: [],
  variables: [],
  worklists: [],
  dependencies: [],
  safety_assumptions: [],
  steps: [
    {
      id: "step_001",
      index: 1,
      group: "Transfer",
      operation: "aspirate",
      name: "Aspirate",
      target_labware: "SourcePlate",
      volume_ul_expression: { kind: "number_literal", value: 20 },
      liquid_class: "Water",
      parameters: {}
    }
  ]
};

assert.equal(PROTOCOL_IR_VERSION, "tecan.protocol_ir.v2");
assert.equal(PROTOCOL_IR_SCHEMA_ID, "https://fluent-ai-assistance.local/schemas/protocol_ir.v2.schema.json");
assert.ok(PROTOCOL_IR_OPERATIONS.includes("query_variable"));
assert.ok(isProtocolIrDocument(validIr));
assert.equal(validateProtocolIr(validIr).issues.length, 0);

const invalidIr = {
  ...validIr,
  ir_version: "tecan.protocol_ir.v1",
  steps: [
    {
      id: "step_001",
      index: 1,
      group: "Transfer",
      operation: "aspirate",
      name: "Aspirate",
      liquid_class: "Water",
      parameters: {}
    }
  ]
};
const invalidIssues = validateProtocolIr(invalidIr).issues;
assert.ok(invalidIssues.some((issue) => issue.path === "$.ir_version"));
assert.ok(invalidIssues.some((issue) => issue.message.includes("target_labware")));
assert.ok(invalidIssues.some((issue) => issue.message.includes("volume_ul")));

const invalidArtifact: SourceArtifact = {
  id: "invalid-protocol-ir",
  name: "invalid.protocol-ir.json",
  kind: "protocol-ir",
  source: "test",
  path: "invalid.protocol-ir.json",
  text: JSON.stringify({ ...invalidIr, ir_version: PROTOCOL_IR_VERSION }),
  size: 0
};
assert.ok(
  buildProtocolModel([invalidArtifact]).warnings.some((warning) => warning.includes("Protocol IR contract failed")),
  "simulator parser should reject malformed current Protocol IR"
);

console.log("protocol IR contract test passed");
