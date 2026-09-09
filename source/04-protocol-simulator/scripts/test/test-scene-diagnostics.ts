import assert from "node:assert/strict";
import { buildSceneDiagnostics } from "../../src/sim/sceneDiagnostics";
import type { ProtocolModel } from "../../src/types";

const model: ProtocolModel = {
  id: "diag-test",
  name: "Diagnostics Test",
  context: "unit-test",
  worktableName: "WT_Test",
  sourceSummaries: [],
  labware: [
    {
      id: "lw-1",
      label: "SourcePlate",
      catalogName: "96 Well Flat",
      geometryName: "96 Well Flat",
      geometrySource: "catalog",
      role: "labware",
      source: "IR",
      location: "NestPlatform",
      position: 1,
      slotIndex: 0,
      rows: 8,
      cols: 12,
      wellShape: "round",
      capacityUl: 200,
      maxVolumeUl: 200,
      deadVolumeUl: 0,
      physicalWidthMm: 127,
      physicalDepthMm: 85,
      physicalHeightMm: 14,
      pitchXMm: 9,
      pitchYMm: 9,
      wellDiameterMm: 6.5,
      wellDepthMm: 10,
      color: "#4e79a7",
      transform: null,
      wells: [],
      hardwareProfile: "plate",
      hardwareAssetIds: [],
      hardwareAssetNames: [],
      modelAssetPath: "/models/fluent/missing-guid.glb",
      liquidCapable: true
    },
    {
      id: "lw-2",
      label: "FoundPlate",
      catalogName: "384 Well",
      geometryName: "384 Well",
      geometrySource: "catalog",
      role: "labware",
      source: "IR",
      location: "NestPlatform",
      position: 2,
      slotIndex: 1,
      rows: 16,
      cols: 24,
      wellShape: "square",
      capacityUl: 50,
      maxVolumeUl: 50,
      deadVolumeUl: 0,
      physicalWidthMm: 127,
      physicalDepthMm: 85,
      physicalHeightMm: 14,
      pitchXMm: 4.5,
      pitchYMm: 4.5,
      wellDiameterMm: 3.5,
      wellDepthMm: 10,
      color: "#ffffff",
      transform: null,
      wells: [],
      hardwareProfile: "plate",
      hardwareAssetIds: [],
      hardwareAssetNames: [],
      modelAssetPath: "/models/fluent/found-guid.glb",
      liquidCapable: true
    }
  ],
  commands: [
    {
      id: "step_001",
      index: 0,
      source: "IR",
      operation: "prompt_user",
      family: "prompt",
      name: "Prompt User",
      group: "Arm verification",
      commandId: "UserPrompt",
      effect: "opaque",
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
      message: "Make sure the fingers on the arm are parallel and not diagonal.",
      rawXml: false,
      verificationOverlay: {
        kind: "rga_fingers",
        expect: "parallel",
        badStates: ["diagonal", "crossed"],
        title: "RGA finger orientation",
        detail: "Fingers should stay parallel."
      }
    }
  ],
  snapshots: [
    {
      index: 0,
      activeCommandId: "step_001",
      tipState: {
        picked: false,
        adapter: false,
        volumeUl: 0,
        activeLabware: "",
        headKind: "unknown",
        channelCount: null,
        activeTipCount: 0,
        tipSpacingXmm: null,
        tipSpacingYmm: null
      },
      gripperState: {
        mounted: true,
        fingerSet: "plate",
        orientation: "parallel",
        jaw: "open",
        grippedLabware: [],
        grippedStack: []
      },
      labwareVolumes: {},
      labwareLocations: {}
    }
  ],
  hardware: {
    assets: 0,
    packagedAssets: 0,
    connectors: 0,
    pins: 0,
    missingAssets: [],
    notes: [],
    assetDetails: []
  },
  repairs: {
    summary: { overallSeverity: "info", blocking: 0, needsReview: 1, safe: 0, info: 0 },
    sourceWorktable: "WT_Source",
    protocolWorktable: "WT_Test",
    sourceCounts: {},
    protocolCounts: {},
    issues: [],
    aliasRepairs: [],
    manualSteps: [],
    validationChecks: [],
    validationGates: [
      {
        id: "xscr_compiles",
        gate: "Gate 9",
        gateNumber: 9,
        name: ".xscr compiles",
        status: "passed",
        severity: "safe",
        summary: "Compile succeeded.",
        details: {},
        source: "ready_validation"
      }
    ]
  },
  warnings: ["Example warning"],
  effectCounts: {}
};

function testHappyPath() {
  const diagnostics = buildSceneDiagnostics({
    model,
    activeIndex: 0,
    meshInventory: {
      missingItems: [{ guid: "missing-guid", name: "missing_mesh", assetPath: "/models/fluent/missing-guid.glb" }],
      fallbackCount: 1
    },
    geometryCoverage: {
      transformedWellLabware: 0,
      exactTransformedWellLabware: 0,
      commandedWellTargets: 0,
      exactCommandedWellTargets: 0,
      anchoredProtocolLocations: 0,
      fallbackCommandedTargets: []
    },
    foundModelAssetPaths: ["/models/fluent/found-guid.glb"]
  });

  assert.equal(diagnostics.kind, "scene-diagnostics");
  assert.equal(diagnostics.activeCommand?.operation, "prompt_user");
  assert.equal(diagnostics.promptTargets.length, 1);
  assert.equal(diagnostics.promptTargets[0]?.overlayKind, "rga_fingers");
  assert.equal(diagnostics.objects.length, 2);

  const sourcePlate = diagnostics.objects.find(o => o.label === "SourcePlate")!;
  assert.equal(sourcePlate.render.mode, "primitive_fallback");

  const foundPlate = diagnostics.objects.find(o => o.label === "FoundPlate")!;
  assert.equal(foundPlate.render.mode, "glb", "Should resolve GLB correctly when in foundModelAssetPaths");

  assert.equal(diagnostics.unresolvedModels.length, 1);
  assert.equal(diagnostics.validationGates.length, 1);
  assert.ok(diagnostics.warnings.includes("Example warning"));
}

function testIndexClamping() {
  const diagnostics = buildSceneDiagnostics({
    model,
    activeIndex: 999, // out of bounds
  });

  assert.equal(diagnostics.playback.activeIndex, 0, "Index should be clamped to 0 since commandCount is 1");
  assert.equal(diagnostics.activeCommand?.id, "step_001", "Should retrieve the clamped active command");
}

function testPlacementOverrides() {
  const diagnostics = buildSceneDiagnostics({
    model,
    activeIndex: 0,
    placementOverrides: [
      {
        label: "SourcePlate",
        position: { x: 100, y: 0, z: 50 },
        rotationY: Math.PI / 2
      }
    ]
  });

  const obj = diagnostics.objects.find(o => o.label === "SourcePlate")!;
  assert.equal(obj.position.source, "placement_override");
  assert.deepEqual(obj.position.scene, { x: 100, y: 0, z: 50 });
  assert.equal(obj.position.rotationY, Math.PI / 2);
}

function testGeometryCoverageFallbacks() {
  const diagnostics = buildSceneDiagnostics({
    model,
    activeIndex: 0,
    geometryCoverage: {
      transformedWellLabware: 0,
      exactTransformedWellLabware: 0,
      commandedWellTargets: 0,
      exactCommandedWellTargets: 0,
      anchoredProtocolLocations: 0,
      fallbackCommandedTargets: ["TargetPlate_123"]
    }
  });

  const targetFallback = diagnostics.fallbackMeshes.find(f => f.label === "TargetPlate_123");
  assert.ok(targetFallback, "Should include a fallback mesh for fallbackCommandedTargets");
  assert.equal(targetFallback.reason, "Command target lacks exact per-well geometry; simulator uses catalog/grid fallback wells.");
}

testHappyPath();
testIndexClamping();
testPlacementOverrides();
testGeometryCoverageFallbacks();

console.log("scene diagnostics tests passed");
