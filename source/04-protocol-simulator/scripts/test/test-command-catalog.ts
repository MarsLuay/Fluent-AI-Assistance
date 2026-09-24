import assert from "node:assert/strict";
import { CONTROL_BAR_COMMAND_GROUPS, allControlBarCommandTemplates, controlBarCatalogDiagnostics } from "../../src/data/controlBar";
import { PROTOCOL_IR_OPERATIONS } from "../../src/data/protocolIrContract";
import { protocolIrOperationValidationMessage } from "../../src/data/protocolIrOperations";
import { scriptEditorCommandFromRecord } from "../../src/data/scriptCommandState";
import { enabledCommandsFromScriptCommands } from "../../src/sim/state";

const templates = allControlBarCommandTemplates();
assert.ok(templates.length > 0, "the canonical command catalog should expose authorable templates");
assert.equal(new Set(templates.map((template) => template.id)).size, templates.length, "catalog template IDs must be unique");
assert.ok(
  templates.every((template) => PROTOCOL_IR_OPERATIONS.includes(template.operation as (typeof PROTOCOL_IR_OPERATIONS)[number])),
  "every authorable template must use a generated Protocol IR operation"
);
assert.ok(!templates.some((template) => template.operation === "move_head" || template.operation === "home_axis"));
assert.ok(CONTROL_BAR_COMMAND_GROUPS.every((group) => group.commands.length > 0));
assert.deepEqual(controlBarCatalogDiagnostics(), [], "catalog runtime validation must stay clean");
assert.equal(protocolIrOperationValidationMessage("move_head"), 'Operation "move_head" is not registered in canonical Protocol IR.');

const restoredUnknown = scriptEditorCommandFromRecord({
  id: "restored-unknown",
  name: "Old Move Head",
  operation: "move_head",
  family: "motion",
  enabled: true
});
assert.equal(restoredUnknown.operation, "move_head", "restoration must preserve unknown source operation for review");
assert.equal(restoredUnknown.family, "opaque", "unknown restored operations must use the opaque review family");
assert.equal(protocolIrOperationValidationMessage(restoredUnknown.operation)?.includes("not registered"), true);
assert.ok(restoredUnknown.validationMessages.some((message) => message.includes("not registered")), "restoration must emit an explicit migration diagnostic");

const rebuilt = enabledCommandsFromScriptCommands(
  [
    restoredUnknown,
    {
      ...restoredUnknown,
      id: "valid-comment",
      operation: "comment",
      family: "comment",
      name: "Review note"
    }
  ],
  []
);
assert.deepEqual(rebuilt.map((command) => command.operation), ["comment"], "unknown operations must not reach the rebuilt protocol model");

console.log(`command catalog test passed (${templates.length} canonical templates)`);
