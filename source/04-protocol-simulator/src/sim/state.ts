import type {
  CommandModel,
  HardwareAssetModel,
  LabwareModel,
  LabwareTransferModel,
  ObjectLibraryItem,
  OperationFamily,
  ProtocolModel,
  ScriptEditorCommand,
  ScriptValidationIssue,
  SimulatorEditorState,
  SnapshotModel,
  TipState
} from "../types";
import { applyGripperCommand, cloneGripperState, emptyGripperState } from "./rgaGripper";

export function buildSnapshots(labware: LabwareModel[], commands: CommandModel[]): SnapshotModel[] {
  const normalizedCommands = normalizeCommandSequence(commands);
  const labwareVolumes = initialVolumes(labware, normalizedCommands);
  const snapshots: SnapshotModel[] = [];
  const tipState: TipState = emptyTipState();
  const gripperState = emptyGripperState();
  const labwareLocations: Record<string, LabwareTransferModel> = {};

  normalizedCommands.forEach((command, index) => {
    applyCommand(command, labwareVolumes, tipState);
    applyGripperCommand(command, gripperState);
    applyLabwareTransfer(command, labwareLocations);
    snapshots.push({
      index,
      activeCommandId: command.id,
      tipState: cloneTipState(tipState),
      gripperState: cloneGripperState(gripperState),
      labwareVolumes: cloneVolumes(labwareVolumes),
      labwareLocations: cloneLabwareLocations(labwareLocations)
    });
  });

  if (!snapshots.length) {
    snapshots.push({
      index: 0,
      activeCommandId: "",
      tipState: cloneTipState(tipState),
      gripperState: cloneGripperState(gripperState),
      labwareVolumes: cloneVolumes(labwareVolumes),
      labwareLocations: {}
    });
  }

  return snapshots;
}

export function emptyTipState(): TipState {
  return {
    picked: false,
    adapter: false,
    volumeUl: 0,
    activeLabware: "",
    headKind: "unknown",
    channelCount: null,
    activeTipCount: 0,
    tipSpacingXmm: null,
    tipSpacingYmm: null
  };
}

export function normalizeCommandSequence(commands: CommandModel[]): CommandModel[] {
  return commands.map((command, index) => ({
    ...command,
    id: command.id || `command-${index + 1}`,
    index
  }));
}

export function enabledCommandsFromScriptCommands(
  scriptCommands: ScriptEditorCommand[],
  sourceCommands: CommandModel[]
): CommandModel[] {
  const sourceById = new Map(sourceCommands.map((command) => [command.id, command]));
  const sourceByCommandId = new Map(sourceCommands.map((command) => [command.commandId, command]));

  return normalizeCommandSequence(
    scriptCommands
      .filter((command) => command.enabled)
      .map((command, index) => {
        const source = command.sourceCommandId ? sourceById.get(command.sourceCommandId) : undefined;
        const fallback = source || sourceByCommandId.get(command.id) || emptyCommand(index);
        const operation = command.operation || fallback.operation;
        const family = command.family || familyForOperation(operation);
        return {
          ...fallback,
          id: command.sourceCommandId || command.id || fallback.id || `edited-command-${index + 1}`,
          name: command.name || fallback.name,
          operation,
          family,
          commandId: command.sourceCommandId ? fallback.commandId || operation : operation,
          effect: command.sourceCommandId && fallback.operation === operation ? fallback.effect : effectForOperation(operation, family),
          targetLabware: command.targetLabware,
          wells: [...command.wells],
          volumeUl: command.volumeUl,
          liquidClass: command.liquidClass,
          message: command.message || fallback.message
        };
      })
  );
}

export function scriptCommandsFromProtocolCommands(commands: CommandModel[]): ScriptEditorCommand[] {
  return normalizeCommandSequence(commands).map((command) => {
    const validationMessages = validateScriptCommand(command);
    return {
      id: `script-${command.id || command.index}`,
      sourceCommandId: command.id,
      name: command.name,
      operation: command.operation,
      family: command.family,
      targetLabware: command.targetLabware,
      wells: [...command.wells],
      volumeUl: command.volumeUl,
      liquidClass: command.liquidClass,
      message: command.message,
      specs: {},
      enabled: true,
      dirty: false,
      validationIssues: scriptValidationIssues(validationMessages),
      validationMessages
    };
  });
}

export function rebuildProtocolWithScriptCommands(
  model: ProtocolModel,
  scriptCommands: ScriptEditorCommand[]
): ProtocolModel {
  const commands = enabledCommandsFromScriptCommands(scriptCommands, model.commands);
  return {
    ...model,
    commands,
    snapshots: buildSnapshots(model.labware, commands)
  };
}

export function createSimulatorEditorState(model: ProtocolModel): SimulatorEditorState {
  return {
    version: 1,
    scriptCommands: scriptCommandsFromProtocolCommands(model.commands),
    objectLibrary: objectLibraryFromModel(model),
    placementOverrides: [],
    imageOverrides: [],
    notes: []
  };
}

export function objectLibraryFromModel(model: ProtocolModel): ObjectLibraryItem[] {
  const hardwareItems = (model.hardware.assetDetails || []).map(objectLibraryItemFromHardwareAsset);
  const labwareItems = model.labware.map(objectLibraryItemFromLabware);
  const byKey = new Map<string, ObjectLibraryItem>();

  [...hardwareItems, ...labwareItems].forEach((item) => {
    const key = `${item.profile}:${normalizeLibraryText(item.name)}:${item.modelAssetPath || ""}`;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, item);
      return;
    }
    byKey.set(key, {
      ...existing,
      imageSrc: existing.imageSrc || item.imageSrc,
      modelAssetPath: existing.modelAssetPath || item.modelAssetPath,
      tags: uniqueStrings([...existing.tags, ...item.tags])
    });
  });

  return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name));
}

function objectLibraryItemFromHardwareAsset(asset: HardwareAssetModel): ObjectLibraryItem {
  return {
    id: `hardware-${asset.id}`,
    name: asset.name,
    profile: asset.profile,
    componentRole: asset.componentRole,
    modelAssetPath: asset.modelAssetPath,
    imageSrc: asset.imageSrc,
    tags: uniqueStrings([
      asset.profile,
      asset.componentRole || "",
      asset.status,
      asset.meshName || "",
      asset.meshGuid || "",
      ...asset.matchedLabware,
      ...asset.references.slice(0, 8)
    ])
  };
}

function objectLibraryItemFromLabware(labware: LabwareModel): ObjectLibraryItem {
  return {
    id: `labware-${labware.id}`,
    name: labware.catalogName || labware.label,
    profile: labware.hardwareProfile,
    componentRole: labware.componentRole,
    modelAssetPath: labware.modelAssetPath,
    tags: uniqueStrings([
      labware.label,
      labware.catalogName,
      labware.geometryName,
      labware.hardwareProfile,
      labware.componentRole || "",
      labware.meshName || "",
      labware.meshGuid || "",
      labware.source,
      labware.role
    ])
  };
}

function applyLabwareTransfer(command: CommandModel, labwareLocations: Record<string, LabwareTransferModel>): void {
  if (!command.labwareTransfer?.labware) return;
  labwareLocations[command.labwareTransfer.labware] = { ...command.labwareTransfer };
}

function initialVolumes(labware: LabwareModel[], commands: CommandModel[]): Record<string, Record<string, number>> {
  const volumes: Record<string, Record<string, number>> = {};
  labware.forEach((item) => {
    volumes[item.label] = {};
    item.wells.forEach((well) => {
      volumes[item.label][well.id] = Math.max(0, well.volumeUl || 0);
    });
  });

  commands.forEach((command) => {
    if (!command.volumeUl || !command.targetLabware || !isLiquidCommand(command.operation)) return;
    const wells = selectedWells(labware, command);
    const target = volumes[command.targetLabware] || {};
    volumes[command.targetLabware] = target;
    wells.forEach((well) => {
      const current = target[well] || 0;
      if (isAspirate(command.operation)) target[well] = current + command.volumeUl!;
      if (isDispense(command.operation)) target[well] = Math.max(0, current - command.volumeUl!);
    });
  });

  return cloneVolumes(volumes);
}

function applyCommand(command: CommandModel, volumes: Record<string, Record<string, number>>, tipState: TipState): void {
  applyHeadMetadata(command, tipState);

  if (command.operation.includes("get_head_adapter")) {
    tipState.adapter = true;
    tipState.activeLabware = command.targetLabware;
    return;
  }

  if (command.operation.includes("drop_head_adapter")) {
    tipState.adapter = false;
    tipState.activeLabware = command.targetLabware;
    return;
  }

  if (command.operation.includes("pick_up") || command.operation.includes("get_tips")) {
    tipState.picked = true;
    tipState.activeTipCount = activeTipCountForCommand(command);
    tipState.activeLabware = command.targetLabware;
    return;
  }

  if (command.operation.includes("drop_tips") || command.operation.includes("set_tips_back")) {
    tipState.picked = false;
    tipState.volumeUl = 0;
    tipState.activeTipCount = 0;
    tipState.activeLabware = command.targetLabware;
    return;
  }

  if (!command.volumeUl || !command.targetLabware || !isLiquidCommand(command.operation)) {
    tipState.activeLabware = command.targetLabware;
    return;
  }

  const labwareVolumes = volumes[command.targetLabware] || {};
  volumes[command.targetLabware] = labwareVolumes;
  const wells = command.wells.length ? command.wells : Object.keys(labwareVolumes);
  wells.forEach((well) => {
    const current = labwareVolumes[well] || 0;
    if (isAspirate(command.operation)) labwareVolumes[well] = Math.max(0, current - command.volumeUl!);
    if (isDispense(command.operation)) labwareVolumes[well] = current + command.volumeUl!;
  });

  if (isAspirate(command.operation)) tipState.volumeUl += command.volumeUl * Math.max(1, wells.length);
  if (isDispense(command.operation)) tipState.volumeUl = Math.max(0, tipState.volumeUl - command.volumeUl * Math.max(1, wells.length));
  tipState.activeTipCount = activeTipCountForCommand(command);
  tipState.activeLabware = command.targetLabware;
}

function applyHeadMetadata(command: CommandModel, tipState: TipState): void {
  if (command.headKind !== "unknown") tipState.headKind = command.headKind;
  if (command.channelCount !== null) tipState.channelCount = command.channelCount;
  if (command.tipSpacingXmm !== null) tipState.tipSpacingXmm = command.tipSpacingXmm;
  if (command.tipSpacingYmm !== null) tipState.tipSpacingYmm = command.tipSpacingYmm;
}

function activeTipCountForCommand(command: CommandModel): number {
  return command.activeTipCount ?? (command.selectedTipIndexes.length || command.wells.length || command.channelCount || 0);
}

function selectedWells(labware: LabwareModel[], command: CommandModel): string[] {
  if (command.wells.length) return command.wells;
  const target = labware.find((item) => item.label === command.targetLabware);
  return target?.wells.map((well) => well.id) || [];
}

function cloneTipState(tipState: TipState): TipState {
  return { ...tipState };
}

function cloneVolumes(volumes: Record<string, Record<string, number>>): Record<string, Record<string, number>> {
  return Object.fromEntries(Object.entries(volumes).map(([label, wells]) => [label, { ...wells }]));
}

function cloneLabwareLocations(locations: Record<string, LabwareTransferModel>): Record<string, LabwareTransferModel> {
  return Object.fromEntries(Object.entries(locations).map(([label, location]) => [label, { ...location }]));
}

function validateScriptCommand(command: CommandModel): string[] {
  const messages: string[] = [];
  if (!command.name.trim()) messages.push("Command name is empty.");
  if (!command.operation.trim()) messages.push("Operation is empty.");
  if (command.family === "liquid" && !command.targetLabware.trim()) messages.push("Liquid command has no target labware.");
  if (command.family === "liquid" && command.volumeUl !== null && command.volumeUl <= 0) messages.push("Liquid volume should be greater than 0 uL.");
  return messages;
}

function scriptValidationIssues(messages: string[]): ScriptValidationIssue[] {
  return messages.map((message) => ({ field: "general", message }));
}

function emptyCommand(index: number): CommandModel {
  return {
    id: `edited-command-${index + 1}`,
    index,
    source: "editor",
    operation: "comment",
    family: "comment",
    name: "Edited Command",
    group: "Editor",
    commandId: "comment",
    effect: "no_effect",
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
    rawXml: false
  };
}

function familyForOperation(operation: string): OperationFamily {
  const lower = operation.toLowerCase();
  if (isAspirate(lower) || isDispense(lower) || lower.includes("mix")) return "liquid";
  if (lower.includes("tip")) return "tips";
  if (lower.includes("move") || lower.includes("transfer_labware")) return "motion";
  if (lower.includes("wash")) return "wash";
  if (lower.includes("prompt") || lower.includes("user")) return "prompt";
  if (lower.includes("loop") || lower.includes("if") || lower.includes("goto")) return "flow";
  if (lower.includes("labware")) return "labware";
  if (lower.includes("comment")) return "comment";
  return "opaque";
}

function effectForOperation(operation: string, family: OperationFamily): string {
  const lower = operation.toLowerCase();
  if (isAspirate(lower)) return "aspirate";
  if (isDispense(lower)) return "dispense";
  if (lower.includes("mix")) return "mix";
  if (family === "tips") return "tip_state";
  if (family === "motion" || family === "labware") return "move";
  if (family === "wash") return "wash";
  if (family === "prompt") return "prompt";
  if (family === "flow") return "flow";
  if (family === "comment") return "annotation";
  return "opaque";
}

function isLiquidCommand(operation: string): boolean {
  return isAspirate(operation) || isDispense(operation) || operation.includes("mix");
}

function isAspirate(operation: string): boolean {
  return operation.toLowerCase().includes("aspirate");
}

function isDispense(operation: string): boolean {
  return operation.toLowerCase().includes("dispense");
}

function normalizeLibraryText(value: string): string {
  return value.toLowerCase().replace(/[_/\\-]+/g, " ").replace(/\s+/g, " ").trim();
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}
