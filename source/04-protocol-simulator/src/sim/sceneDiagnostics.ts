import { verificationOverlayForStep } from "./rgaGripper";
import { SCENE_DIAGNOSTICS_KIND, SCENE_DIAGNOSTICS_SCHEMA_VERSION } from "../types";
import type {
  CommandModel,
  LabwareModel,
  Matrix3Model,
  ProtocolModel,
  SceneDiagnosticsModel,
  SceneFocusTarget,
  ScenePlacementOverride,
  VerificationStepModel,
  Vector3Model
} from "../types";

const SLOT_COLUMNS = 6;
const SLOT_WIDTH = 7.2;
const SLOT_DEPTH = 5.2;
const DECK_WIDTH = SLOT_COLUMNS * SLOT_WIDTH + 4;
const DECK_DEPTH = 22;
const FLUENT_SCENE_SCALE = 0.08;

export type MeshInventoryDiagnosticInput = {
  missingItems: Array<{
    guid: string;
    name: string;
    assetPath: string;
  }>;
  fallbackCount: number;
};

export type GeometryCoverageDiagnosticInput = {
  transformedWellLabware: number;
  exactTransformedWellLabware: number;
  commandedWellTargets: number;
  exactCommandedWellTargets: number;
  anchoredProtocolLocations: number;
  fallbackCommandedTargets: string[];
};

export type SceneDiagnosticsInput = {
  model: ProtocolModel;
  activeIndex: number;
  placementOverrides?: ScenePlacementOverride[];
  meshInventory?: MeshInventoryDiagnosticInput;
  geometryCoverage?: GeometryCoverageDiagnosticInput;
  verificationSteps?: VerificationStepModel[];
  sceneFocus?: SceneFocusTarget | null;
  staticMode?: boolean;
  foundModelAssetPaths?: string[];
};

export function buildSceneDiagnostics(input: SceneDiagnosticsInput): SceneDiagnosticsModel {
  const { model, activeIndex } = input;
  const commandCount = model.commands.length;
  const safeIndex = clampIndex(activeIndex, commandCount);
  const activeCommand = itemAtIndex(model.commands, safeIndex);
  const snapshot = itemAtIndex(model.snapshots, safeIndex);
  const overrideByLabel = placementOverrideMap(input.placementOverrides || []);
  const foundAssets = new Set((input.foundModelAssetPaths || []).map(normalizeAssetPath));
  const missingAssetPaths = new Set((input.meshInventory?.missingItems || []).map((item) => normalizeAssetPath(item.assetPath)));

  const objects = model.labware.map((labware) =>
    objectDiagnosticFor(labware, overrideByLabel.get(labware.label), foundAssets, missingAssetPaths)
  );

  const unresolvedModels = buildUnresolvedModels(objects, input.meshInventory);
  const fallbackMeshes = buildFallbackMeshes(objects, input.meshInventory, input.geometryCoverage);

  return {
    exportedAt: new Date().toISOString(),
    app: "tecan-protocol-simulator",
    kind: "scene-diagnostics" as typeof SCENE_DIAGNOSTICS_KIND,
    schemaVersion: SCENE_DIAGNOSTICS_SCHEMA_VERSION,
    protocol: {
      id: model.id,
      name: model.name,
      worktableName: model.worktableName,
      context: model.context
    },
    playback: {
      activeIndex: safeIndex,
      commandCount,
      staticMode: input.staticMode
    },
    activeCommand: activeCommand ? activeCommandDiagnostic(activeCommand, snapshot?.gripperState) : null,
    promptTargets: promptTargetsFor(model.commands, input.verificationSteps || []),
    objects,
    unresolvedModels,
    fallbackMeshes,
    warnings: [...model.warnings],
    validationGates: model.repairs.validationGates.map((gate) => ({
      id: gate.id,
      gate: gate.gate,
      gateNumber: gate.gateNumber,
      name: gate.name,
      status: gate.status,
      severity: gate.severity,
      summary: gate.summary,
      source: gate.source
    })),
    geometryCoverage: input.geometryCoverage,
    gripper: snapshot?.gripperState,
    sceneFocus: input.sceneFocus || null
  };
}

function objectDiagnosticFor(
  labware: LabwareModel,
  override: ScenePlacementOverride | undefined,
  foundAssets: Set<string>,
  missingAssetPaths: Set<string>
): SceneDiagnosticsModel["objects"][number] {
  const position = positionDiagnosticFor(labware, override);
  const modelAssetPath = normalizeAssetPath(labware.modelAssetPath || "");
  const modelAssetStatus = modelAssetStatusFor(modelAssetPath, foundAssets, missingAssetPaths);
  const usesGlb = Boolean(modelAssetPath) && modelAssetStatus === "found";
  const renderMode =
    labware.role === "editor_object" || labware.source === "object-library"
      ? "editor_object"
      : usesGlb
        ? "glb"
        : "primitive_fallback";

  return {
    id: labware.id,
    label: labware.label,
    catalogName: labware.catalogName,
    role: labware.role,
    componentRole: labware.componentRole,
    location: labware.location,
    positionIndex: labware.position,
    position,
    render: {
      mode: renderMode,
      modelAssetPath: modelAssetPath || undefined,
      modelAssetStatus,
      meshGuid: labware.meshGuid,
      meshName: labware.meshName
    },
    liquidCapable: labware.liquidCapable,
    anchored: labware.role === "protocol_target" && Boolean(labware.location) && !labware.transform
  };
}

function positionDiagnosticFor(labware: LabwareModel, override?: ScenePlacementOverride): SceneDiagnosticsModel["objects"][number]["position"] {
  if (override) {
    return {
      source: "placement_override",
      scene: roundVector(override.position),
      rotationY: override.rotationY
    };
  }
  if (labware.transform?.positionMm) {
    const fluentMm = roundVector(labware.transform.positionMm);
    return {
      source: "fluent_transform",
      fluentMm,
      scene: fluentToSceneVector(fluentMm),
      rotationY: yawFromFluentOrientation(labware.transform.orientation)
    };
  }
  return {
    source: "slot_grid",
    scene: roundVector(slotPositionFor(labware)),
    rotationY: 0
  };
}

function activeCommandDiagnostic(command: CommandModel, gripperState?: SceneDiagnosticsModel["gripper"]): SceneDiagnosticsModel["activeCommand"] {
  return {
    id: command.id,
    index: command.index,
    operation: command.operation,
    family: command.family,
    name: command.name,
    group: command.group,
    targetLabware: command.targetLabware,
    message: command.message,
    motionKind: command.motionKind,
    verificationOverlay: command.verificationOverlay,
    gripperState
  };
}

function promptTargetsFor(commands: CommandModel[], verificationSteps: VerificationStepModel[]): SceneDiagnosticsModel["promptTargets"] {
  return commands
    .map((command, commandIndex) => {
      const overlay = verificationOverlayForStep(command, verificationSteps) || command.verificationOverlay;
      const isPrompt = command.family === "prompt" || command.operation === "prompt_user" || Boolean(overlay);
      if (!isPrompt) return null;
      const prompt = stripReferenceMedia(command.message || "");
      if (!prompt && !overlay) return null;
      return {
        commandId: command.id,
        commandIndex,
        stepId: command.id,
        group: command.group,
        prompt,
        overlayKind: overlay?.kind,
        expect: overlay?.expect,
        badStates: overlay?.badStates,
        focusLabware: command.targetLabware || undefined
      };
    })
    .filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));
}

function buildUnresolvedModels(
  objects: SceneDiagnosticsModel["objects"],
  meshInventory?: MeshInventoryDiagnosticInput
): SceneDiagnosticsModel["unresolvedModels"] {
  const unresolved: SceneDiagnosticsModel["unresolvedModels"] = [];

  objects.forEach((object) => {
    if (object.render.modelAssetStatus !== "missing" || !object.render.modelAssetPath) return;
    unresolved.push({
      label: object.label,
      meshGuid: object.render.meshGuid,
      meshName: object.render.meshName,
      modelAssetPath: object.render.modelAssetPath,
      reason: "Referenced GLB asset was not found on disk."
    });
  });

  (meshInventory?.missingItems || []).forEach((mesh) => {
    if (unresolved.some((entry) => normalizeAssetPath(entry.modelAssetPath) === normalizeAssetPath(mesh.assetPath))) return;
    unresolved.push({
      meshGuid: mesh.guid,
      meshName: mesh.name,
      modelAssetPath: mesh.assetPath,
      reason: "Worktable mesh has no converted GLB asset."
    });
  });

  return unresolved;
}

function buildFallbackMeshes(
  objects: SceneDiagnosticsModel["objects"],
  meshInventory?: MeshInventoryDiagnosticInput,
  geometryCoverage?: GeometryCoverageDiagnosticInput
): SceneDiagnosticsModel["fallbackMeshes"] {
  const fallbacks: SceneDiagnosticsModel["fallbackMeshes"] = objects
    .filter((object) => object.render.mode === "primitive_fallback")
    .map((object) => ({
      label: object.label,
      meshGuid: object.render.meshGuid,
      meshName: object.render.meshName,
      modelAssetPath: object.render.modelAssetPath,
      reason: object.render.modelAssetPath
        ? "Model asset missing; scene uses primitive fallback geometry."
        : "No model asset path; scene uses primitive fallback geometry."
    }));

  (geometryCoverage?.fallbackCommandedTargets || []).forEach((label) => {
    if (fallbacks.some((entry) => entry.label === label)) return;
    fallbacks.push({
      label,
      reason: "Command target lacks exact per-well geometry; simulator uses catalog/grid fallback wells."
    });
  });

  if (meshInventory?.fallbackCount) {
    meshInventory.missingItems.forEach((mesh) => {
      if (fallbacks.some((entry) => entry.meshGuid === mesh.guid)) return;
      fallbacks.push({
        meshGuid: mesh.guid,
        meshName: mesh.name,
        modelAssetPath: mesh.assetPath,
        reason: "Converted mesh asset missing; related scene objects may use primitive fallback bodies."
      });
    });
  }

  return fallbacks;
}

function modelAssetStatusFor(path: string, foundAssets: Set<string>, missingAssetPaths: Set<string>): SceneObjectRenderDiagnostic["modelAssetStatus"] {
  if (!path) return "none";
  const normalized = normalizeAssetPath(path);
  if (foundAssets.has(normalized)) return "found";
  if (missingAssetPaths.has(normalized)) return "missing";
  return "not_checked";
}

function placementOverrideMap(overrides: ScenePlacementOverride[]): Map<string, ScenePlacementOverride> {
  const map = new Map<string, ScenePlacementOverride>();
  overrides.forEach((override) => {
    const label = override.labwareLabel || override.label;
    if (label) map.set(label, override);
  });
  return map;
}

function slotPositionFor(labware: LabwareModel): Vector3Model {
  const slot = Math.max(0, labware.position ? labware.position - 1 : labware.slotIndex);
  const x = -DECK_WIDTH / 2 + 3.8 + (slot % SLOT_COLUMNS) * SLOT_WIDTH;
  const z = -DECK_DEPTH / 2 + 2.6 + Math.floor(slot / SLOT_COLUMNS) * SLOT_DEPTH;
  return { x, y: 0, z };
}

function fluentToSceneVector(position: Vector3Model): Vector3Model {
  return {
    x: round(position.x * FLUENT_SCENE_SCALE),
    y: round(position.z * FLUENT_SCENE_SCALE),
    z: round(-position.y * FLUENT_SCENE_SCALE)
  };
}

function yawFromFluentOrientation(orientation: Matrix3Model): number {
  const row = orientation[0];
  if (!Array.isArray(row) || row.length < 3) return 0;
  return round(Math.atan2(row[2], row[0]));
}

function stripReferenceMedia(message: string): string {
  return message.replace(/\n?\[Reference media:[\s\S]*$/i, "").trim();
}

function normalizeAssetPath(path: string): string {
  return path.replace(/\\/g, "/").trim();
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function roundVector(vector: Vector3Model): Vector3Model {
  return { x: round(vector.x), y: round(vector.y), z: round(vector.z) };
}

function clampIndex(index: number, count: number): number {
  if (count <= 0) return 0;
  return Math.max(0, Math.min(index, count - 1));
}

function itemAtIndex<T>(items: T[], index: number): T | undefined {
  if (!items.length) return undefined;
  return items[clampIndex(index, items.length)];
}

type SceneObjectRenderDiagnostic = SceneDiagnosticsModel["objects"][number]["render"];
