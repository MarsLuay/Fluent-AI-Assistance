import assert from "node:assert/strict";
import {
  emptyGripperState,
  cloneGripperState,
  applyGripperCommand,
  enrichIrCommandWithRgaCues,
  isRgaRelevantCommand,
  labwareTransferFromIrMovePlate,
  verificationOverlayFromText,
  verificationOverlayForStep,
  verificationStepsFromGenerationPayload
} from "../../src/sim/rgaGripper";
import type { CommandModel, GripperState, VerificationStepModel, LabwareTransferModel } from "../../src/types";

function createCommand(overrides: Partial<CommandModel> = {}): CommandModel {
  return {
    id: "test-cmd",
    index: 0,
    source: "IR",
    operation: "",
    family: "motion",
    name: "Test",
    group: "",
    commandId: "test",
    effect: "",
    targetLabware: "",
    wells: [],
    volumeUl: null,
    liquidClass: "",
    headKind: "unknown",
    channelCount: null,
    activeTipCount: null,
    selectedTipIndexes: [],
    tipSpacingXmm: null,
    tipSpacingYmm: null,
    message: "",
    motionKind: "unknown",
    ...overrides
  };
}

async function runTests() {
  console.log("🧪 Testing rgaGripper.ts module");

  // Test emptyGripperState
  const emptyState = emptyGripperState();
  assert.equal(emptyState.mounted, false);
  assert.equal(emptyState.fingerSet, "unknown");
  assert.equal(emptyState.orientation, "unknown");
  assert.equal(emptyState.jaw, "unknown");
  assert.deepEqual(emptyState.grippedLabware, []);
  assert.deepEqual(emptyState.grippedStack, []);

  // Test cloneGripperState
  const originalState: GripperState = {
    mounted: true,
    fingerSet: "plate",
    orientation: "parallel",
    jaw: "closed",
    grippedLabware: ["plate1"],
    grippedStack: ["plate1", "anchor"]
  };
  const clonedState = cloneGripperState(originalState);
  assert.deepEqual(clonedState, originalState);
  assert.notStrictEqual(clonedState, originalState);
  assert.notStrictEqual(clonedState.grippedLabware, originalState.grippedLabware);
  assert.notStrictEqual(clonedState.grippedStack, originalState.grippedStack);

  // applyGripperCommand tests

  // drop finger
  const dropFingerCmd = createCommand({ operation: "drop finger" });
  applyGripperCommand(dropFingerCmd, clonedState);
  assert.equal(clonedState.mounted, false);
  assert.equal(clonedState.jaw, "open");
  assert.deepEqual(clonedState.grippedLabware, []);

  // move_plate
  const gripper1 = emptyGripperState();
  const movePlateCmd = createCommand({ operation: "move_plate", targetLabware: "plate1" });
  applyGripperCommand(movePlateCmd, gripper1);
  assert.equal(gripper1.mounted, true);
  assert.equal(gripper1.fingerSet, "plate"); // default
  assert.equal(gripper1.jaw, "closed");
  assert.equal(gripper1.orientation, "parallel");
  assert.deepEqual(gripper1.grippedLabware, ["plate1"]);
  assert.deepEqual(gripper1.grippedStack, ["plate1"]);

  // move_plate with anchor
  const gripper2 = emptyGripperState();
  const movePlateAnchorCmd = createCommand({
    operation: "move_plate",
    labwareTransfer: {
      labware: "plate2",
      anchorLabware: "anchor1",
      location: "loc",
      site: "1",
      fixedSite: true,
      moveToBase: false,
      destinationKind: "fixed_site"
    } as LabwareTransferModel
  });
  applyGripperCommand(movePlateAnchorCmd, gripper2);
  assert.equal(gripper2.mounted, true);
  assert.equal(gripper2.fingerSet, "plate");
  assert.equal(gripper2.jaw, "closed");
  assert.equal(gripper2.orientation, "parallel");
  assert.deepEqual(gripper2.grippedLabware, ["plate2"]);
  assert.deepEqual(gripper2.grippedStack, ["anchor1", "plate2"]);

  // call_subroutine
  const gripper3 = emptyGripperState();
  const getFingersCmd = createCommand({ operation: "call_subroutine", message: "get_fingers tube" });
  applyGripperCommand(getFingersCmd, gripper3);
  assert.equal(gripper3.mounted, true);
  assert.equal(gripper3.fingerSet, "tube");
  assert.equal(gripper3.jaw, "open");
  assert.equal(gripper3.orientation, "parallel");

  // call_subroutine - cap
  const gripperCap = emptyGripperState();
  const capCmd = createCommand({ operation: "call_subroutine", message: "cap scan" });
  applyGripperCommand(capCmd, gripperCap);
  assert.equal(gripperCap.mounted, true);
  assert.equal(gripperCap.fingerSet, "cap");
  assert.equal(gripperCap.jaw, "closed");

  // prompt_user rga_fingers
  const gripper4 = emptyGripperState();
  const promptRgaFingersCmd = createCommand({
    operation: "prompt_user",
    verificationOverlay: {
      kind: "rga_fingers",
      expect: "parallel",
      badStates: [],
      title: "",
      detail: ""
    }
  });
  applyGripperCommand(promptRgaFingersCmd, gripper4);
  assert.equal(gripper4.mounted, true);
  assert.equal(gripper4.fingerSet, "plate");
  assert.equal(gripper4.jaw, "open");
  assert.equal(gripper4.orientation, "parallel");

  // prompt_user labware_seating
  const gripper5 = cloneGripperState(originalState);
  const promptSeatingCmd = createCommand({
    operation: "prompt_user",
    verificationOverlay: {
      kind: "labware_seating",
      expect: "seated_flat",
      badStates: [],
      title: "",
      detail: ""
    }
  });
  applyGripperCommand(promptSeatingCmd, gripper5);
  assert.equal(gripper5.jaw, "open");
  assert.deepEqual(gripper5.grippedLabware, []);
  assert.deepEqual(gripper5.grippedStack, []);

  // labwareTransferFromIrMovePlate tests
  const params1 = { labware: "plate1", destination_location: "loc1" };
  const transfer1 = labwareTransferFromIrMovePlate("move_plate", params1, "");
  assert.ok(transfer1);
  assert.equal(transfer1.labware, "plate1");
  assert.equal(transfer1.destinationKind, "fixed_site");

  const params2 = { labware: "plate1", onto_labware: "loc1" };
  const transfer2 = labwareTransferFromIrMovePlate("move_plate", params2, "");
  assert.ok(transfer2);
  assert.equal(transfer2.labware, "plate1");
  assert.equal(transfer2.destinationKind, "cover_site");

  const transferNull = labwareTransferFromIrMovePlate("not_move_plate", params2, "");
  assert.equal(transferNull, null);

  // verificationOverlayFromText tests
  const overlay1 = verificationOverlayFromText("check rga fingers are parallel", "", "");
  assert.ok(overlay1);
  assert.equal(overlay1.kind, "rga_fingers");

  const overlay2 = verificationOverlayFromText("make sure it is seated flat", "", "");
  assert.ok(overlay2);
  assert.equal(overlay2.kind, "labware_seating");

  const overlay3 = verificationOverlayFromText("tube gripper close", "", "");
  assert.ok(overlay3);
  assert.equal(overlay3.kind, "tube_cap_gripper");

  // verificationOverlayForStep tests
  const overlayStep1 = createCommand({ id: "s1" });
  const verificationSteps: VerificationStepModel[] = [
    { type: "prompt", prompt: "seated flat", group: "", stepId: "s1" }
  ];
  const overlayResolved = verificationOverlayForStep(overlayStep1, verificationSteps);
  assert.ok(overlayResolved);
  assert.equal(overlayResolved.kind, "labware_seating");

  // isRgaRelevantCommand tests
  assert.equal(isRgaRelevantCommand(undefined, undefined), false);
  assert.equal(isRgaRelevantCommand(undefined, { mounted: true } as GripperState), true);
  assert.equal(isRgaRelevantCommand(createCommand({ motionKind: "rga" }), undefined), true);
  assert.equal(isRgaRelevantCommand(createCommand({ operation: "move_plate" }), undefined), true);
  assert.equal(isRgaRelevantCommand(createCommand({ operation: "pipette" }), { mounted: false } as GripperState), false);

  // enrichIrCommandWithRgaCues tests
  const cmdToEnrich = createCommand({
    id: "e1",
    message: "original",
    operation: "move_plate",
    targetLabware: "target1"
  });
  const enriched = enrichIrCommandWithRgaCues(cmdToEnrich, {
    message: "fingers on the arm",
    destination_location: "loc2"
  });
  assert.equal(enriched.message, "fingers on the arm");
  assert.equal(enriched.motionKind, "rga");
  assert.ok(enriched.verificationOverlay);
  assert.equal(enriched.verificationOverlay.kind, "rga_fingers");
  assert.ok(enriched.labwareTransfer);
  assert.equal(enriched.labwareTransfer.location, "loc2");

  // verificationStepsFromGenerationPayload tests
  const payload = {
    generation: {
      verification_steps: [
        { type: "prompt", prompt: "test prompt", step_id: "123" },
        { invalid_step: true } // should be filtered out
      ]
    }
  };
  const steps = verificationStepsFromGenerationPayload(payload);
  assert.equal(steps.length, 1);
  assert.equal(steps[0].type, "prompt");
  assert.equal(steps[0].prompt, "test prompt");
  assert.equal(steps[0].stepId, "123");

  console.log("✅ All tests passed.");
}

runTests().catch((err) => {
  console.error("❌ Test failed:", err);
  process.exit(1);
});
