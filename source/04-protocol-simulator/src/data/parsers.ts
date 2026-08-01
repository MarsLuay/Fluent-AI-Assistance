import type {
  ArtifactKind,
  CommandModel,
  HeadKind,
  HardwareAssetModel,
  HardwareContext,
  HardwareProfile,
  LabwareDefinitionModel,
  LabwareModel,
  LabwareTransferModel,
  OperationFamily,
  ParsedBundle,
  ProtocolBuildOptions,
  ProtocolModel,
  SnapSiteModel,
  SourceArtifact,
  Vector3Model,
  WellShape,
  WorktableIssue,
  WorktableIssueKind,
  WorktableRepairModel,
  WorktableMeshModel,
  ModelAssetModel,
  WellModel
} from "../types";
import type { ResolvedLabwareGeometry } from "./labwareCatalog";
import { hardwareProfileFromZeia, inferCatalogNameFromLabel, hasZeiaLabwareCatalog, registerLabwareCatalogFromDefinitions, registerLabwareCatalogPayload, resolveLabwareGeometry } from "./labwareCatalog";
import { registerFluentConnectorGraph } from "./fluentConnectorCompatibility";
import { buildSnapshots } from "../sim/state";
import { enrichIrCommandWithRgaCues } from "../sim/rgaGripper";
import { isWorktableGeometryName, parseFluentGeometryArtifacts } from "./fluentGeometry";
import { artifactSelectionKey } from "./sourceCatalog";
import { PROTOCOL_IR_VERSION, validateProtocolIr } from "./protocolIrContract";
import {
  completeValidationGates,
  issueFromValidationGate,
  readinessFromRecord,
  validationGatesFromMarkdown,
  validationGatesFromReadyReport,
  validationGatesFromRequestDiff
} from "./readinessParser";

export {
  completeValidationGates,
  issueFromValidationGate,
  readinessFromRecord,
  validationGatesFromMarkdown,
  validationGatesFromReadyReport,
  validationGatesFromRequestDiff
} from "./readinessParser";
export { validationGateFromRecord } from "./readinessParser";

const EMPTY_HARDWARE: HardwareContext = {
  assets: 0,
  packagedAssets: 0,
  connectors: 0,
  pins: 0,
  missingAssets: [],
  notes: [],
  assetDetails: [],
  worktableMeshes: [],
  modelAssets: []
};

const EMPTY_REPAIRS: WorktableRepairModel = {
  summary: {
    overallSeverity: "info",
    blocking: 0,
    needsReview: 0,
    safe: 0,
    info: 0
  },
  sourceWorktable: "",
  protocolWorktable: "",
  sourceCounts: {},
  protocolCounts: {},
  issues: [],
  aliasRepairs: [],
  manualSteps: [],
  validationChecks: [],
  validationGates: []
};

const DEFAULT_ROWS = 8;

type CommandHeadMetadata = Pick<
  CommandModel,
  "headKind" | "channelCount" | "activeTipCount" | "selectedTipIndexes" | "tipSpacingXmm" | "tipSpacingYmm"
>;

type CreateLabwareInput = {
  label: string;
  catalogName?: string;
  source: string;
  slotIndex: number;
  role?: string;
  functionalGroup?: string;
  location?: string;
  position?: number | null;
  positionExpression?: string;
  transform?: LabwareModel["transform"];
  meshGuid?: string;
  meshName?: string;
  meshSourcePath?: string;
  meshPayloadSize?: number;
  meshDecodedSize?: number | null;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  modelAssetPath?: string;
  modelAssetFormat?: LabwareModel["modelAssetFormat"];
  wellSites?: SnapSiteModel[];
};

type FluentComponentKind =
  | "labware"
  | "tube_rack"
  | "tip_box"
  | "reservoir"
  | "wash"
  | "waste"
  | "cap_holder"
  | "adapter"
  | "nest"
  | "carrier"
  | "device"
  | "structural"
  | "unknown_component";

const GENERIC_FLUENT_COMPONENT_CATALOG = "Adapter";

export function detectArtifactKind(name: string, text = ""): ArtifactKind {
  const lower = name.toLowerCase();
  if (lower.endsWith(".zeia") || lower.endsWith(".zip")) return "zeia";
  if (/\.(jpg|jpeg|png|gif|webp)$/i.test(lower)) return "hardware-image";
  if (lower.endsWith(".xmsh")) return "worktable-mesh";
  if (isWorktableGeometryName(lower)) return "worktable-geometry";
  if (lower.endsWith(".xscr") || text.trimStart().startsWith("<?xml")) return "xscr";
  if (lower.endsWith(".gwl")) return "gwl";
  if ((lower.includes("validation_report") || lower.includes("ready_validation")) && lower.endsWith(".md")) return "validation-diff";
  if (lower.includes("repair_plan") && lower.endsWith(".md")) return "repair-plan";
  if (lower.endsWith(".yaml") || lower.endsWith(".yml")) return lower.includes("alias") ? "alias-map" : "unknown";
  if (lower.includes("labware_catalog") && lower.endsWith(".json")) return "labware-catalog";
  if (
    (lower.includes("connector_graph") || lower.includes("connector-graph")) &&
    lower.endsWith(".json")
  ) {
    return "connector-graph";
  }
  if (lower.includes("hardware_manifest") && lower.endsWith(".json")) return "hardware";
  if (lower.includes("worktable") && lower.endsWith(".json")) return "worktable-diff";
  if ((lower.includes("validation_diff") || lower.includes("validation_report") || lower.includes("ready_validation")) && lower.endsWith(".json")) return "validation-diff";
  if (lower.includes("repair") && lower.endsWith(".json")) return "repair-plan";
  if (lower.includes("generation_manifest") && lower.endsWith(".json")) return "metadata";
  if (lower.includes("metadata") && lower.endsWith(".json")) return "metadata";
  if (lower.includes("simulation") && lower.endsWith(".json")) return "simulation";
  if (lower.endsWith("protocol.ir.json") || lower.endsWith("protocol-ir.json")) return "protocol-ir";

  if (lower.endsWith(".json")) {
    try {
      const payload = JSON.parse(text) as Record<string, unknown>;
      if (payload.app === "tecan-protocol-simulator") {
        if (payload.kind === "sim-scene") return "metadata";
        if (payload.kind === "scene-diagnostics") return "metadata";
        if (payload.kind === "simulator-project" || payload.kind === "edited-command-queue") return "simulation";
      }
      if (payload.kind === "edited-command-queue" || payload.protocol) return "simulation";
      if (payload.ir_version || (payload.steps && payload.labware && payload.worktable)) return "protocol-ir";
      if (payload.kind === "worktable_patch" || payload.kind === "worktable_diff" || payload.operations) return "worktable-diff";
      if (payload.kind === "request_validation_diff" || payload.checks || payload.gates || payload.validation_version) return "validation-diff";
      if (payload.actions && payload.summary) return "repair-plan";
      if (payload.final_labware || payload.snapshots || payload.effect_counts) return "simulation";
      if (payload.asset_artifacts || payload.connector_artifacts || payload.pins) return "hardware";
      if (payload.kind === "fluent-connector-graph") return "connector-graph";
    } catch {
      return "unknown";
    }
  }

  return "unknown";
}

export function buildProtocolModel(artifacts: SourceArtifact[], options: ProtocolBuildOptions = {}): ProtocolModel {
  for (const artifact of artifacts) {
    if (artifact.kind === "labware-catalog" || /labware_catalog\.json$/i.test(artifact.name) || /labware_catalog\.json$/i.test(artifact.path || "")) {
      try {
        registerLabwareCatalogPayload(JSON.parse(artifact.text || "{}"));
      } catch {
        // Ignore malformed catalog artifacts; ZEIA geometry registration still runs below.
      }
    }
    if (
      artifact.kind === "connector-graph" ||
      /connector_graph\.json$/i.test(artifact.name) ||
      /connector_graph\.json$/i.test(artifact.path || "")
    ) {
      try {
        registerFluentConnectorGraph(JSON.parse(artifact.text || "{}"));
      } catch {
        // Ignore malformed graphs; local install rebuild still applies.
      }
    }
  }
  const geometryBundle = parseFluentGeometryBundle(artifacts);
  registerLabwareCatalogFromDefinitions(geometryBundle.labwareDefinitions || []);
  const artifactBundles = artifacts.map(parseArtifact);
  const bundles = [...artifactBundles, geometryBundle];
  const hardware = mergeHardware(bundles.map((bundle) => bundle.hardware));
  const worktableMeshes = mergeWorktableMeshes([
    ...bundles.flatMap((bundle) => bundle.worktableMeshes || []),
    ...(hardware.worktableMeshes || [])
  ]);
  const modelAssets = mergeModelAssets([
    ...bundles.flatMap((bundle) => bundle.modelAssets || []),
    ...(hardware.modelAssets || [])
  ]);
  const linkedHardwareSeed: HardwareContext = {
    ...hardware,
    worktableMeshes,
    modelAssets
  };
  const labwareDefinitions = bundles.flatMap((bundle) => bundle.labwareDefinitions || []);
  const labware = linkHardwareAssetsToLabware(mergeLabware(enrichLabwareWithDefinitions(bundles.flatMap((bundle) => bundle.labware), labwareDefinitions)), hardware.assetDetails);
  const linkedHardware = linkLabwareToHardware(linkedHardwareSeed, labware);
  const primaryCommands = choosePrimaryCommands(artifactBundles, artifacts, bundles, options.primaryScriptArtifactKey);
  const commands = primaryCommands.length ? primaryCommands : fallbackCommands(labware);
  const metadata: Partial<ProtocolModel> = Object.assign({}, ...bundles.map((bundle) => bundle.metadata));
  const repairs = mergeRepairs(bundles.map((bundle) => bundle.repairs));
  const warnings = bundles.flatMap((bundle) => bundle.warnings);
  if (!hasZeiaLabwareCatalog()) {
    warnings.push(
      "Missing ZEIA labware_catalog.json / .xcmp labware definitions — dims and mesh GUIDs are not invented. Import a full ZEIA package (source/labware_catalog.json) or Components .xcmp."
    );
  }
  const effectCounts = mergeEffectCounts(bundles.map((bundle) => bundle.effectCounts), commands);
  const sourceSummaries = artifacts.map((artifact, index) => ({
    name: artifact.name,
    kind: artifact.kind,
    count: artifact.kind === "xscr" || artifact.kind === "gwl" ? artifactBundles[index].commands.length : 1
  }));

  const snapshots = buildSnapshots(labware, commands);

  return {
    id: String(metadata.id || "local-protocol"),
    name: String(metadata.name || "Local Protocol"),
    context: String(metadata.context || "local workspace"),
    worktableName: String(metadata.worktableName || "Worktable"),
    readinessStatus: typeof metadata.readinessStatus === "string" ? metadata.readinessStatus : undefined,
    readiness: metadata.readiness || null,
    sourceSummaries,
    labware,
    commands,
    snapshots,
    hardware: {
      ...linkedHardware,
      worktableMeshes,
      modelAssets
    },
    repairs,
    warnings,
    effectCounts,
    worktableMeshes,
    modelAssets
  };
}

function parseArtifact(artifact: SourceArtifact): ParsedBundle {
  if (artifact.kind === "protocol-ir") return parseProtocolIr(artifact);
  if (artifact.kind === "xscr") return parseXscr(artifact);
  if (artifact.kind === "gwl") return parseGwl(artifact);
  if (artifact.kind === "simulation") return parseSimulation(artifact);
  if (artifact.kind === "labware-catalog") return emptyBundle();
  if (artifact.kind === "connector-graph") return emptyBundle();
  if (artifact.kind === "hardware") return parseHardware(artifact);
  if (artifact.kind === "worktable-geometry" || artifact.kind === "worktable-mesh") return emptyBundle();
  if (artifact.kind === "hardware-image") return parseHardwareImage(artifact);
  if (artifact.kind === "metadata") return parseMetadata(artifact);
  if (artifact.kind === "worktable-diff") return parseWorktableDiff(artifact);
  if (artifact.kind === "validation-diff") return parseValidationDiff(artifact);
  if (artifact.kind === "repair-plan") return parseRepairPlan(artifact);
  if (artifact.kind === "alias-map") return parseAliasMap(artifact);
  return emptyBundle();
}

function parseFluentGeometryBundle(artifacts: SourceArtifact[]): ParsedBundle {
  const parsed = parseFluentGeometryArtifacts(artifacts);
  const bundle = emptyBundle();

  const worktableMeshes = parsed.meshes.map((mesh) => worktableMeshFromFluentMetadata(mesh));
  const modelAssets = parsed.meshes
    .filter((mesh) => mesh.modelAssetPath)
    .map((mesh) => modelAssetFromFluentMeshMetadata(mesh));

  bundle.metadata = parsed.metadata;
  bundle.hardware = {
    connectors: parsed.hardware.connectors,
    assets: modelAssets.length,
    packagedAssets: modelAssets.length,
    worktableMeshes,
    modelAssets,
    notes: worktableMeshes.length ? [`Loaded ${worktableMeshes.length} Fluent worktable mesh metadata record${worktableMeshes.length === 1 ? "" : "s"}.`] : []
  };
  bundle.worktableMeshes = worktableMeshes;
  bundle.modelAssets = modelAssets;
  bundle.warnings = parsed.warnings;
  bundle.labwareDefinitions = parsed.labwareDefinitions;
  bundle.labware = parsed.labware.map((record, index) => {
    const normalized = normalizeFluentLabwareInput({
      label: record.label,
      catalogName: record.catalogName,
      source: record.transform.source,
      slotIndex: index,
        role: record.role,
        functionalGroup: record.functionalGroup,
        location: record.location,
        position: record.position,
        transform: {
          ...record.transform,
          meshGuid: record.meshGuid,
          meshName: record.meshName,
          meshSourcePath: record.meshSourcePath,
          meshPayloadSize: record.meshPayloadSize,
          meshDecodedSize: record.meshDecodedSize ?? undefined,
          unitScaleToMm: record.unitScaleToMm,
          unitScaleSource: record.unitScaleSource,
          nativeUnit: record.nativeUnit,
          modelAssetPath: record.modelAssetPath,
          modelAssetFormat: record.modelAssetPath ? "glb" : undefined
        },
        meshGuid: record.meshGuid,
        meshName: record.meshName,
        meshSourcePath: record.meshSourcePath,
        meshPayloadSize: record.meshPayloadSize,
        meshDecodedSize: record.meshDecodedSize,
        unitScaleToMm: record.unitScaleToMm,
        unitScaleSource: record.unitScaleSource,
        nativeUnit: record.nativeUnit,
        modelAssetPath: record.modelAssetPath,
        modelAssetFormat: record.modelAssetPath ? "glb" : undefined,
        wellSites: record.wellSites
      });
    return createLabware(normalized);
  });

  return bundle;
}


function worktableMeshFromFluentMetadata(mesh: {
  guid: string;
  name: string;
  sourcePath: string;
  payloadSize: number;
  decodedSize: number | null;
  base64Length: number;
  modelAssetPath: string;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
}): WorktableMeshModel {
  return {
    id: slug(`mesh-${mesh.guid || mesh.name || mesh.sourcePath}`),
    guid: mesh.guid,
    name: mesh.name || mesh.guid || displayAssetName(mesh.sourcePath),
    objectName: mesh.name || mesh.guid || displayAssetName(mesh.sourcePath),
    source: "Fluent ZEIA",
    sourcePath: mesh.sourcePath,
    payloadSize: mesh.payloadSize,
    decodedSize: mesh.decodedSize ?? undefined,
    base64Length: mesh.base64Length,
    payloadEncoding: mesh.base64Length > 0 ? "base64" : "unknown",
    convertedAssetPath: mesh.modelAssetPath,
    modelAssetPath: mesh.modelAssetPath,
    modelAssetFormat: mesh.modelAssetPath ? "glb" : undefined,
    unitScaleToMm: mesh.unitScaleToMm,
    unitScaleSource: mesh.unitScaleSource,
    nativeUnit: mesh.nativeUnit,
    references: [mesh.sourcePath, mesh.modelAssetPath].filter(Boolean),
    notes: []
  };
}

function modelAssetFromFluentMeshMetadata(mesh: {
  guid: string;
  name: string;
  sourcePath: string;
  modelAssetPath: string;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
}): ModelAssetModel {
  return {
    id: slug(`model-${mesh.guid || mesh.modelAssetPath || mesh.name}`),
    name: mesh.name || mesh.guid || displayAssetName(mesh.modelAssetPath),
    path: mesh.modelAssetPath,
    format: mesh.modelAssetPath.toLowerCase().endsWith(".gltf") ? "gltf" : "glb",
    source: "Fluent mesh conversion target",
    meshGuid: mesh.guid,
    meshName: mesh.name,
    unitScaleToMm: mesh.unitScaleToMm,
    unitScaleSource: mesh.unitScaleSource,
    nativeUnit: mesh.nativeUnit,
    references: [mesh.sourcePath, mesh.modelAssetPath].filter(Boolean)
  };
}

function emptyBundle(): ParsedBundle {
  return {
    labware: [],
    commands: [],
    labwareDefinitions: [],
    metadata: {},
    hardware: {},
    repairs: {},
    warnings: [],
    effectCounts: {}
  };
}

function parseProtocolIr(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  try {
    const ir = JSON.parse(artifact.text) as Record<string, unknown>;
    // v2 is the canonical transport contract. Keep legacy v1 display support
    // for historical exports, but do not project malformed current IR into the
    // simulator model.
    if (ir.ir_version === PROTOCOL_IR_VERSION) {
      const contract = validateProtocolIr(ir);
      if (contract.issues.length) {
        const first = contract.issues[0];
        throw new Error(`Protocol IR contract failed at ${first.path}: ${first.message}`);
      }
    }
    const protocol = objectAt(ir, "protocol");
    const source = objectAt(ir, "source");
    const worktable = objectAt(ir, "worktable");

    bundle.metadata = {
      id: stringAt(ir, "id") || artifact.name,
      name: stringAt(protocol, "name") || stringAt(protocol, "comment") || artifact.name,
      context: stringAt(source, "context") || stringAt(source, "context_kind") || artifact.source,
      worktableName: stringAt(worktable, "name") || stringAt(worktable, "guid") || "Worktable"
    };

    const irLabware = arrayAt(ir, "labware").map((item, index) => labwareFromRecord(item, "IR", index));
    const rawXmlLabware = arrayAt(ir, "steps").flatMap((step, index) => labwareFromRawXmlStep(step, index));
    bundle.labware = [...irLabware, ...rawXmlLabware];

    bundle.commands = arrayAt(ir, "steps").map((step, index) => commandFromIrStep(step, index));
  } catch (error) {
    bundle.warnings.push(`Could not parse ${artifact.name}: ${errorMessage(error)}`);
  }
  return bundle;
}

function parseXscr(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  try {
    const document = new DOMParser().parseFromString(artifact.text, "application/xml");
    const parserError = document.querySelector("parsererror");
    if (parserError) throw new Error(parserError.textContent || "XML parser error");

    bundle.metadata = {
      name: textOf(document, "ObjectName") || artifact.name,
      context: artifact.source,
      worktableName: textOf(document, "BaseWorkspaceName") || textOf(document, "ObjectName") || "XSCR worktable"
    };

    const objects = Array.from(document.querySelectorAll("Object"));
    const labware: LabwareModel[] = [];
    const commands: CommandModel[] = [];
    const variables = new Map<string, string>();
    const unhandledCommands = new Set<string>();
    let commandIndex = 0;
    let activeGroup = "";

    objects.forEach((node) => {
      const type = node.getAttribute("Type") || "";
      const localName = type.split(".").pop() || node.firstElementChild?.tagName || "";
      if (localName.includes("ScriptGroupData")) {
        activeGroup = textOf(node, "Name") || activeGroup;
        return;
      }

      if (localName.includes("AddLabwareData")) {
        labware.push(labwareFromRecord(recordFromXscrNode(node), "XSCR", labware.length));
      }

      if (localName.includes("SetVariableStatement")) {
        const variableName = textOf(node, "Name");
        if (variableName) variables.set(variableName, unquoteXscrValue(textOf(node, "Value")));
      }

      const operation = operationFromCommandId(localName);
      if (operation) {
        commands.push(commandFromXscrNode(node, commandIndex, activeGroup, localName, variables));
        commandIndex += 1;
      } else if (/command|statement/i.test(localName)) {
        // Surface command-like nodes we don't model so they aren't silently
        // dropped from the queue (data sub-objects without command/statement
        // in their type are skipped to avoid noise).
        unhandledCommands.add(localName);
      }
    });

    bundle.labware = labware;
    bundle.commands = commands;
    if (unhandledCommands.size) {
      bundle.warnings.push(
        `Unsupported XSCR command type(s) skipped in ${artifact.name}: ${Array.from(unhandledCommands).sort().slice(0, 20).join(", ")}`
      );
    }
  } catch (error) {
    bundle.warnings.push(`Could not parse ${artifact.name}: ${errorMessage(error)}`);
  }
  return bundle;
}

function parseGwl(artifact: SourceArtifact): ParsedBundle {
  const commands: CommandModel[] = [];
  const labwareByLabel = new Map<string, LabwareModel>();

  artifact.text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line, index) => {
      const fields = line.split(";").map((field) => field.trim());
      const code = fields[0]?.toUpperCase() || "";
      const operation = code === "A" ? "aspirate" : code === "D" ? "dispense" : code === "W" ? "wash" : code === "C" ? "comment" : "";
      if (!operation) return;

      // Tecan GWL columns: code;RackLabel;RackID;RackType;Position;TubeID;Volume;LiquidClass;...
      const label = fields[1] || "";
      const catalog = fields[3] || inferCatalogName(label);
      const well = wellFromGwlPosition(fields[4] || "");
      const wells = well ? [well] : [];
      // Volume lives in the Volume column (index 6); never fall back to columns
      // at/left of it (TubeID, Position, RackID) which would mis-read a barcode
      // or well number as the volume.
      const volume = numericField([fields[6]]) ?? numericField(fields.slice(6));
      const liquidClass = fields[7] || fields.find((field) => /water|serum|dna|liquid|wash|buffer/i.test(field)) || "";
      const headMetadata = headMetadataFromRecord({ operation, command_id: code }, {}, emptyHeadMetadata(), wells);

      if (label && !labwareByLabel.has(label)) {
        labwareByLabel.set(
          label,
          createLabware({
            label,
            catalogName: catalog,
            source: "GWL",
            slotIndex: labwareByLabel.size
          })
        );
      }

      commands.push({
        id: `gwl-${index + 1}`,
        index,
        source: "GWL",
        operation,
        family: familyForOperation(operation),
        name: code === "C" ? "Worklist Comment" : titleCase(operation),
        group: "Worklist",
        commandId: code,
        effect: operation === "comment" ? "no_effect" : operation === "wash" ? "tip_state_change" : "liquid_transfer",
        targetLabware: label,
        wells,
        volumeUl: operation === "wash" || operation === "comment" ? null : volume,
        liquidClass,
        ...headMetadata,
        message: fields.slice(1).filter(Boolean).join(" "),
        rawXml: false
      });
    });

  return {
    ...emptyBundle(),
    labware: Array.from(labwareByLabel.values()),
    commands
  };
}

function parseSimulation(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  try {
    const simulation = JSON.parse(artifact.text) as Record<string, unknown>;
    const exportedProtocol = asRecord(simulation.protocol);
    if (Object.keys(exportedProtocol).length) {
      const exportedLabware = arrayAt(exportedProtocol, "labware") as unknown as LabwareModel[];
      const exportedCommands = arrayAt(exportedProtocol, "commands") as unknown as CommandModel[];
      bundle.labware = exportedLabware;
      bundle.commands = exportedCommands;
      bundle.effectCounts = asNumberRecord(exportedProtocol.effectCounts) || asNumberRecord(simulation.effectCounts);
      bundle.hardware = asRecord(exportedProtocol.hardware) as Partial<HardwareContext>;
      bundle.repairs = asRecord(exportedProtocol.repairs) as Partial<WorktableRepairModel>;
      bundle.warnings = Array.isArray(exportedProtocol.warnings) ? exportedProtocol.warnings.map((warning) => String(warning)) : [];
      bundle.worktableMeshes = Array.isArray(exportedProtocol.worktableMeshes) ? (exportedProtocol.worktableMeshes as WorktableMeshModel[]) : [];
      bundle.modelAssets = Array.isArray(exportedProtocol.modelAssets) ? (exportedProtocol.modelAssets as ModelAssetModel[]) : [];
      bundle.metadata = {
        context: stringAt(exportedProtocol, "context") || "simulator export",
        id: stringAt(exportedProtocol, "id") || "simulator-export",
        name: stringAt(exportedProtocol, "name") || artifact.name,
        worktableName: stringAt(exportedProtocol, "worktableName") || "Worktable"
      };
      return bundle;
    }

    const finalLabware = objectAt(simulation, "final_labware");
    bundle.labware = Object.entries(finalLabware).map(([label, state], index) => {
      const stateRecord = asRecord(state);
      const labware = createLabware({
        label,
        catalogName: stringAt(stateRecord, "catalog_name") || inferCatalogName(label),
        source: "simulation",
        slotIndex: index,
        location: listAt(stateRecord, "slot")[0] ? String(listAt(stateRecord, "slot")[0]) : "",
        position: numberAtArray(listAt(stateRecord, "slot"), 1)
      });
      const wells = objectAt(stateRecord, "wells");
      labware.wells = labware.wells.map((well) => {
        const wellState = asRecord(wells[well.id]);
        if (!wellState) return well;
        const volumeUl = numberAt(wellState, "volume_ul") ?? well.volumeUl;
        const layers = arrayAt(wellState, "layers").map((layer) => ({
          reagent: stringAt(layer, "reagent") || "liquid",
          volumeUl: numberAt(layer, "volume_ul") ?? 0
        }));
        return {
          ...well,
          volumeUl,
          reagent: layers[0]?.reagent || well.reagent,
          layers
        };
      });
      return labware;
    });

    bundle.commands = arrayAt(simulation, "steps").map((step, index) => commandFromSimulationStep(step, index));
    bundle.effectCounts = asNumberRecord(simulation.effect_counts);
    bundle.metadata = {
      context: "simulation",
      name: artifact.name
    };
  } catch (error) {
    bundle.warnings.push(`Could not parse ${artifact.name}: ${errorMessage(error)}`);
  }
  return bundle;
}

function parseHardware(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  try {
    const hardware = JSON.parse(artifact.text) as Record<string, unknown>;
    const assets = arrayAt(hardware, "assets");
    const assetArtifacts = arrayAt(hardware, "asset_artifacts");
    const connectors = arrayAt(hardware, "connector_artifacts");
    const pins = arrayAt(hardware, "pins");
    bundle.hardware = {
      assets: assets.length || assetArtifacts.length,
      packagedAssets: assets.filter((asset) => stringAt(asset, "status") === "asset_packaged").length || assetArtifacts.length,
      connectors: connectors.length,
      pins: pins.length,
      missingAssets: assets
        .filter((asset) => Boolean(asset.manual_verification_required))
        .map((asset) => stringAt(asset, "asset_name"))
        .filter(Boolean),
      notes: listAt(hardware, "notes").map(String),
      assetDetails: assets.length
        ? assets.map((asset, index) => hardwareAssetFromManifestAsset(asset, index))
        : assetArtifacts.map((asset, index) => hardwareAssetFromArtifact(asset, index))
    };
  } catch (error) {
    bundle.warnings.push(`Could not parse ${artifact.name}: ${errorMessage(error)}`);
  }
  return bundle;
}

function parseHardwareImage(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  const name = displayAssetName(artifact.name);
  bundle.hardware = {
    assetDetails: [
      {
        id: slug(`${artifact.id}-${name}`),
        name,
        status: "asset_packaged",
        packaged: true,
        manualVerificationRequired: false,
        bundlePath: artifact.path || artifact.name,
        sourcePath: artifact.path || artifact.name,
        entry: artifact.path || artifact.name,
        imageSrc: artifact.dataUrl || "",
        references: [artifact.name, artifact.path || ""].filter(Boolean),
        profile: hardwareProfileFromZeia({ nameText: name }),
        matchedLabware: []
      }
    ]
  };
  return bundle;
}

function hardwareAssetFromManifestAsset(asset: Record<string, unknown>, index: number): HardwareAssetModel {
  const artifacts = arrayAt(asset, "asset_artifacts");
  const firstArtifact = artifacts[0] || {};
  const name =
    stringAt(asset, "asset_name") ||
    stringAt(firstArtifact, "object_name") ||
    displayAssetName(stringAt(firstArtifact, "bundle_path") || stringAt(firstArtifact, "entry")) ||
    `Asset ${index + 1}`;
  const status = stringAt(asset, "status") || (artifacts.length ? "asset_packaged" : "referenced");
  const bundlePath = stringAt(firstArtifact, "bundle_path") || stringAt(asset, "bundle_path");
  const sourcePath = stringAt(firstArtifact, "source_path") || stringAt(firstArtifact, "key") || stringAt(asset, "source_path");
  const entry = stringAt(firstArtifact, "entry") || stringAt(asset, "entry");
  const references = [
    ...listAt(asset, "raw_refs").map(String),
    ...listAt(asset, "asset_refs").map(String),
    name,
    bundlePath,
    entry
  ].filter(Boolean);

  return {
    id: slug(`${name}-${bundlePath || entry || index}`),
    name,
    status,
    packaged: status === "asset_packaged" || artifacts.length > 0,
    manualVerificationRequired: Boolean(asset.manual_verification_required),
    bundlePath,
    sourcePath,
    entry,
    imageSrc: "",
    references,
    profile: hardwareProfileFromZeia({ nameText: [name, bundlePath, entry, ...references].join(" ") }),
    matchedLabware: []
  };
}

function hardwareAssetFromArtifact(asset: Record<string, unknown>, index: number): HardwareAssetModel {
  const name =
    stringAt(asset, "object_name") ||
    displayAssetName(stringAt(asset, "bundle_path") || stringAt(asset, "entry") || stringAt(asset, "source_path")) ||
    `Asset ${index + 1}`;
  const bundlePath = stringAt(asset, "bundle_path");
  const sourcePath = stringAt(asset, "source_path") || stringAt(asset, "key");
  const entry = stringAt(asset, "entry");
  const references = [...listAt(asset, "asset_refs").map(String), name, bundlePath, entry].filter(Boolean);

  return {
    id: slug(`${name}-${bundlePath || entry || index}`),
    name,
    status: Boolean(asset.packaged) ? "asset_packaged" : stringAt(asset, "status") || "referenced",
    packaged: Boolean(asset.packaged),
    manualVerificationRequired: false,
    bundlePath,
    sourcePath,
    entry,
    imageSrc: "",
    references,
    profile: hardwareProfileFromZeia({ nameText: [name, bundlePath, entry, ...references].join(" ") }),
    matchedLabware: []
  };
}

function parseMetadata(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  try {
    const metadata = JSON.parse(artifact.text) as Record<string, unknown>;
    bundle.metadata = {
      context: stringAt(metadata, "context_name") || stringAt(metadata, "bundle_schema_version") || artifact.source,
      name:
        stringAt(metadata, "compiled_xscr") ||
        stringAt(metadata, "script_name") ||
        stringAt(metadata, "corrected_xscr_duplicate") ||
        artifact.name,
      readinessStatus: stringAt(metadata, "readiness_status"),
      readiness: readinessFromRecord(metadata)
    };
  } catch {
    bundle.metadata = { context: artifact.source };
  }
  return bundle;
}

function parseWorktableDiff(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  try {
    const diff = JSON.parse(artifact.text) as Record<string, unknown>;
    const source = objectAt(diff, "source");
    const protocol = objectAt(diff, "protocol");
    const sourceWorktable = objectAt(source, "worktable");
    const protocolWorktable = objectAt(protocol, "worktable");
    const issues: WorktableIssue[] = [];

    arrayAt(diff, "operations").forEach((operation, index) => {
      issues.push(issueFromPatchOperation(operation, index));
    });

    arrayAt(diff, "missing_labware").forEach((item, index) => {
      issues.push(
        makeIssue({
          id: `missing-labware-${index}`,
          kind: "missing_labware",
          severity: statusSeverity(stringAt(item, "status") || "missing"),
          status: stringAt(item, "status") || "missing",
          label: stringAt(item, "label") || stringAt(item, "name") || "Missing labware",
          detail: stringAt(item, "catalog") || "Not found in source ZEIA context",
          targetValue: deckLocation(item),
          suggestion: "Add this labware to the selected FluentControl worktable or map it through a labware alias.",
          diffPath: `/missing_labware/${index}`
        })
      );
    });

    arrayAt(diff, "changed_deck_positions").forEach((item, index) => {
      issues.push(
        makeIssue({
          id: `changed-position-${index}`,
          kind: "changed_position",
          severity: "needs_review",
          status: stringAt(item, "status") || "changed",
          label: stringAt(item, "label") || "Changed deck position",
          detail: "Required deck position differs from source context",
          sourceValue: stringAt(item, "source_deck_location"),
          targetValue: stringAt(item, "required_deck_location"),
          suggestion: `Move ${stringAt(item, "label") || "labware"} to ${stringAt(item, "required_deck_location") || "the required deck position"}.`,
          diffPath: `/changed_deck_positions/${index}`
        })
      );
    });

    appendRequirementIssues(issues, diff, "required_liquid_classes", "liquid_class", "Confirm the liquid class exists or add a liquid-class alias.");
    appendRequirementIssues(issues, diff, "device_aliases", "device_alias", "Confirm the device alias exists or add a device-alias mapping.");
    appendRequirementIssues(issues, diff, "worklist_paths", "worklist", "Place the worklist at the expected path or update the IR worklist path.");
    appendRequirementIssues(issues, diff, "required_carriers", "carrier", "Add or alias the required carrier.");
    appendRequirementIssues(issues, diff, "required_tip_boxes", "tip_box", "Add or alias the required tip box.");

    arrayAt(diff, "warnings").forEach((item, index) => {
      issues.push(
        makeIssue({
          id: `worktable-warning-${index}`,
          kind: "warning",
          severity: severityFromValue(stringAt(item, "severity") || "needs_review"),
          status: "warning",
          label: stringAt(item, "id") || "Worktable warning",
          detail: stringAt(item, "message") || stringAt(item, "text"),
          suggestion: "Review in FluentControl before instrument use.",
          diffPath: stringAt(item, "diff_path") || `/warnings/${index}`
        })
      );
    });
    listAt(diff, "warnings")
      .filter((item) => typeof item === "string")
      .forEach((item, index) => {
        issues.push(
          makeIssue({
            id: `worktable-warning-text-${index}`,
            kind: "warning",
            severity: "needs_review",
            status: "warning",
            label: "Worktable warning",
            detail: String(item),
            suggestion: "Review in FluentControl before instrument use.",
            diffPath: `/warnings/${index}`
          })
        );
      });

    bundle.repairs = {
      sourceWorktable: stringAt(sourceWorktable, "name"),
      protocolWorktable: stringAt(protocolWorktable, "name"),
      sourceCounts: countsFrom(source),
      protocolCounts: countsFrom(protocol),
      issues,
      manualSteps: manualStepsFrom(diff),
      summary: summaryFromIssues(issues, diff)
    };
  } catch (error) {
    bundle.warnings.push(`Could not parse ${artifact.name}: ${errorMessage(error)}`);
  }
  return bundle;
}

function parseValidationDiff(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  try {
    if (artifact.name.toLowerCase().endsWith(".md")) {
      const gates = validationGatesFromMarkdown(artifact.text, artifact.name);
      bundle.repairs = {
        validationGates: completeValidationGates(gates, artifact.name),
        issues: gates.filter((gate) => gate.status !== "passed").map(issueFromValidationGate),
        summary: summaryFromIssues(gates.filter((gate) => gate.status !== "passed").map(issueFromValidationGate), {})
      };
      return bundle;
    }

    const validation = JSON.parse(artifact.text) as Record<string, unknown>;
    const directGates = validationGatesFromReadyReport(validation, artifact.name);
    if (directGates.length) {
      const nonPassing = directGates.filter((gate) => gate.status !== "passed");
      bundle.repairs = {
        validationGates: completeValidationGates(directGates, artifact.name),
        issues: nonPassing.map(issueFromValidationGate),
        summary: summaryFromIssues(nonPassing.map(issueFromValidationGate), validation)
      };
      return bundle;
    }

    const checks = arrayAt(validation, "checks").map((check, index) =>
      makeIssue({
        id: stringAt(check, "id") || `validation-${index}`,
        kind: validationIssueKind(stringAt(check, "id")),
        severity: statusSeverity(stringAt(check, "status")),
        status: stringAt(check, "status") || "unknown",
        label: stringAt(check, "id") || "Validation check",
        detail: stringAt(check, "summary"),
        suggestion: stringAt(check, "status") === "passed" ? "No action needed." : "Review the generated validation diff and request spec.",
        diffPath: `/checks/${index}`
      })
    );
    const nestedGates = validationGatesFromRequestDiff(validation, artifact.name);
    bundle.repairs = {
      validationChecks: checks,
      validationGates: nestedGates,
      issues: [...checks.filter((check) => check.status !== "passed"), ...nestedGates.filter((gate) => gate.status !== "passed").map(issueFromValidationGate)],
      summary: summaryFromIssues(checks.filter((check) => check.status !== "passed"), validation)
    };
  } catch (error) {
    bundle.warnings.push(`Could not parse ${artifact.name}: ${errorMessage(error)}`);
  }
  return bundle;
}

function parseRepairPlan(artifact: SourceArtifact): ParsedBundle {
  const bundle = emptyBundle();
  try {
    if (artifact.name.toLowerCase().endsWith(".md")) {
      bundle.repairs = { aliasRepairs: aliasRepairsFromRepairMarkdown(artifact.text, artifact.name) };
      return bundle;
    }

    const plan = JSON.parse(artifact.text) as Record<string, unknown>;
    bundle.repairs = {
      aliasRepairs: arrayAt(plan, "actions").map((action, index) => ({
        id: `repair-${index}`,
        kind: stringAt(action, "kind") || "repair",
        status: stringAt(action, "status") || "unknown",
        oldName: stringAt(action, "old"),
        newName: stringAt(action, "new"),
        summary: stringAt(action, "summary"),
        source: "repair-plan",
        line: numberAt(action, "line")
      }))
    };
  } catch (error) {
    bundle.warnings.push(`Could not parse ${artifact.name}: ${errorMessage(error)}`);
  }
  return bundle;
}

function parseAliasMap(artifact: SourceArtifact): ParsedBundle {
  const aliasRepairs = artifact.text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.endsWith(":") && line.includes(":"))
    .map((line, index) => {
      const [oldName, newName] = parseAliasYamlLine(line);
      return {
        id: `${artifact.name}-${index}`,
        kind: aliasKindFromName(artifact.name),
        status: "ready",
        oldName,
        newName,
        summary: `Configured alias maps ${oldName} to ${newName}.`,
        source: artifact.name,
        line: index + 1
      };
    })
    .filter((repair) => repair.oldName && repair.newName);

  return {
    ...emptyBundle(),
    repairs: { aliasRepairs }
  };
}

function commandFromIrStep(step: Record<string, unknown>, fallbackIndex: number): CommandModel {
  const parameters = objectAt(step, "parameters");
  const operation = stringAt(step, "operation") || "opaque";
  const targetLabware = stringAt(step, "target_labware") || stringAt(parameters, "labware") || stringAt(parameters, "label");
  const rawXml = stringAt(parameters, "raw_xml");
  const rawHeadMetadata = familyForOperation(operation) === "flow" ? emptyHeadMetadata() : headMetadataFromXml(rawXml);
  const wells = wellsFromStep(parameters, operation);
  const volumeUl = numberAt(step, "volume_ul") ?? numberAt(parameters, "volume_ul");
  const liquidClass = stringAt(step, "liquid_class") || stringAt(parameters, "liquid_class");
  const headMetadata = headMetadataFromRecord(step, parameters, rawHeadMetadata, wells);
  const message =
    stringAt(parameters, "prompt") ||
    stringAt(parameters, "message") ||
    stringAt(parameters, "subroutine") ||
    stringAt(parameters, "comment") ||
    stringAt(step, "compiled_path");
  const base: CommandModel = {
    id: stringAt(step, "id") || `ir-${fallbackIndex + 1}`,
    index: numberAt(step, "index") ?? fallbackIndex,
    source: "IR",
    operation,
    family: familyForOperation(operation),
    name: stringAt(step, "name") || titleCase(operation),
    group: stringAt(step, "group"),
    commandId: stringAt(step, "command_id") || operation,
    effect: effectForOperation(operation),
    targetLabware,
    wells,
    volumeUl,
    liquidClass,
    ...headMetadata,
    message,
    rawXml: Boolean(rawXml)
  };
  return enrichIrCommandWithRgaCues(base, parameters);
}

function commandFromXscrNode(node: Element, index: number, group: string, commandId: string, variables: Map<string, string> = new Map()): CommandModel {
  const labwareTransfer = labwareTransferFromXscrNode(node, variables);
  if (labwareTransfer) {
    const lineNumber = transferLineNumber(node) ?? index + 1;
    return {
      id: `xscr-${lineNumber}-${index}`,
      index,
      source: "XSCR",
      operation: "transfer_labware",
      family: "motion",
      name: "Transfer Labware",
      group,
      commandId: transferCommandName(node, commandId),
      effect: "labware_movement",
      targetLabware: labwareTransfer.labware,
      wells: [],
      volumeUl: null,
      liquidClass: "",
      ...emptyHeadMetadata(),
      message: labwareTransferMessage(labwareTransfer),
      rawXml: false,
      labwareTransfer
    };
  }

  const operation = operationFromCommandId(commandId) || "opaque";
  const firstX = numberFromText(node, "FirstTipXPosition");
  const firstY = numberFromText(node, "FirstTipYPosition");
  const lastX = numberFromText(node, "LastTipXPosition") ?? firstX;
  const lastY = numberFromText(node, "LastTipYPosition") ?? firstY;
  const targetLabware =
    textOf(node, "LabwareName") ||
    textOf(node, "LabwareLable") ||
    textOf(node, "LabwareLabel") ||
    textOf(node, "Location");
  const selectedWells = wellsFromSelectionString(textOf(node, "SelectedWellsString"));
  const tipRectangleWells =
    firstX !== null && firstY !== null && lastX !== null && lastY !== null ? wellsFromTipRectangle(firstX, firstY, lastX, lastY) : [];
  const wells = selectedWells.length ? selectedWells : tipRectangleWells;
  const volumeUl = numberFromText(node, "Volume") ?? numberFromText(node, "AspirateVolume") ?? numberFromText(node, "DispenseVolume");
  const liquidClass = textOf(node, "LiquidClassNameBySelection") || textOf(node, "LiquidClassName") || textOf(node, "LiquidClass");
  const lineNumber = numberFromText(node, "LineNumber") ?? index + 1;
  const headMetadata = headMetadataFromXscrNode(node, commandId, wells);

  return {
    id: `xscr-${lineNumber}-${index}`,
    index,
    source: "XSCR",
    operation,
    family: familyForOperation(operation),
    name: titleCase(operation),
    group,
    commandId,
    effect: effectForOperation(operation),
    targetLabware,
    wells,
    volumeUl,
    liquidClass,
    ...headMetadata,
    message: commandId,
    rawXml: false
  };
}

function labwareTransferFromXscrNode(node: Element, variables: Map<string, string>): LabwareTransferModel | null {
  const macro = transferMacroNode(node);
  if (!macro) return null;
  const macroName = transferCommandName(node, "");
  const executionSettings = textOfDirect(macro, "ExecutionSettings");
  if (!/transferlabware/i.test(`${macroName} ${executionSettings}`)) return null;

  const decoded = decodeEmbeddedXscrXml(executionSettings);
  if (!decoded.includes("TransferLabwareCommandParameters")) return null;

  try {
    const document = new DOMParser().parseFromString(decoded, "application/xml");
    if (document.querySelector("parsererror")) return null;
    const labware = textOf(document, "Labware");
    if (!labware) return null;

    const rawLocation = decodeXmlEntities(textOf(document, "Location"));
    const rawSite = decodeXmlEntities(textOf(document, "Site"));
    const location = resolveXscrExpression(rawLocation, variables);
    const site = resolveXscrExpression(rawSite, variables);
    const moveToBase = booleanText(textOf(document, "MoveToBase")) || isBaseLocation(location);
    const fixedSite = booleanText(textOf(document, "FixedSite"));
    const anchorLabware = transferAnchorLabware(rawLocation) || transferAnchorLabware(location) || undefined;

    return {
      labware,
      location,
      site,
      fixedSite,
      moveToBase,
      anchorLabware,
      destinationKind: transferDestinationKind(location, rawLocation, moveToBase, anchorLabware),
      rawLocation,
      rawSite
    };
  } catch {
    return null;
  }
}

function transferCommandName(node: Element, fallback: string): string {
  return transferMacroNode(node)?.getAttribute("Name") || fallback;
}

function transferLineNumber(node: Element): number | null {
  const value = Number(transferMacroNode(node)?.getAttribute("LineNumber"));
  return Number.isFinite(value) ? value : null;
}

function transferMacroNode(node: Element): Element | null {
  const macro = node.firstElementChild;
  if (!macro) return null;
  return elementLocalName(macro) === "applicationdrivermacro" ? macro : null;
}

function textOfDirect(root: Element, tagName: string): string {
  const wanted = tagName.toLowerCase();
  const child = Array.from(root.children).find((item) => elementLocalName(item) === wanted);
  return child?.textContent?.trim() || "";
}

function elementLocalName(element: Element): string {
  return (element.localName || element.tagName.split(":").pop() || element.tagName).toLowerCase();
}

function labwareTransferMessage(transfer: LabwareTransferModel): string {
  const destination = transfer.moveToBase ? "<BASE>" : [transfer.location, transfer.site].filter(Boolean).join(" site ");
  return `Move ${transfer.labware} to ${destination || "unknown destination"}`;
}

function transferDestinationKind(
  location: string,
  rawLocation: string,
  moveToBase: boolean,
  anchorLabware: string | undefined
): LabwareTransferModel["destinationKind"] {
  if (moveToBase || isBaseLocation(location)) return "base";
  if (anchorLabware || /GetCoverSiteName/i.test(rawLocation)) return "cover_site";
  return location ? "fixed_site" : "unknown";
}

function transferAnchorLabware(expression: string): string {
  if (!expression) return "";
  // Cover-site anchor is the quoted GetCoverSiteName label from the script/ZEIA.
  // Do not invent AdapterA200 / CollectionDWP / FilterDWP index forms.
  const quoted = expression.match(/GetCoverSite(?:Name|Index)\("([^"]+)"\)/i)?.[1] || "";
  return quoted.trim();
}

function resolveXscrExpression(value: string, variables: Map<string, string>): string {
  const trimmed = unquoteXscrValue(value);
  const variableValue = variables.get(trimmed);
  return variableValue !== undefined ? unquoteXscrValue(variableValue) : trimmed;
}

function unquoteXscrValue(value: string): string {
  const trimmed = decodeXmlEntities(value).trim();
  return trimmed.replace(/^"(.*)"$/, "$1").trim();
}

function decodeXmlEntities(value: string): string {
  let text = value || "";
  for (let index = 0; index < 4; index += 1) {
    const next = decodeXmlEntitiesOnce(text);
    if (next === text) break;
    text = next;
  }
  return text;
}

function decodeEmbeddedXscrXml(value: string): string {
  let text = value || "";
  for (let index = 0; index < 4; index += 1) {
    const next = decodeXmlEntitiesOnce(text);
    if (next === text) break;
    text = next;
    if (/<TransferLabwareCommandParameters[\s>]/.test(text)) break;
  }
  return text;
}

function decodeXmlEntitiesOnce(value: string): string {
  return (value || "")
    .replace(/&#x([0-9a-fA-F]+);/g, (_match, hex) => codePointFromEntity(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_match, dec) => codePointFromEntity(parseInt(dec, 10)))
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

function codePointFromEntity(codePoint: number): string {
  if (!Number.isFinite(codePoint) || codePoint <= 0 || codePoint > 0x10ffff) return "";
  try {
    return String.fromCodePoint(codePoint);
  } catch {
    return "";
  }
}

function booleanText(value: string): boolean {
  return /^(true|1|yes|y|on)$/i.test(value.trim());
}

function isBaseLocation(location: string): boolean {
  return /^<BASE>$/i.test(location.trim());
}

function commandFromSimulationStep(step: Record<string, unknown>, fallbackIndex: number): CommandModel {
  const operation = stringAt(step, "command_id") || "opaque";
  const headMetadata = headMetadataFromRecord(step, {}, emptyHeadMetadata(), []);
  return {
    id: `sim-${fallbackIndex + 1}`,
    index: numberAt(step, "step_index") ?? fallbackIndex,
    source: "simulation",
    operation,
    family: familyForOperation(operation),
    name: stringAt(step, "step_type") || titleCase(operation),
    group: "Simulation",
    commandId: operation,
    effect: stringAt(step, "effect") || effectForOperation(operation),
    targetLabware: "",
    wells: [],
    volumeUl: null,
    liquidClass: "",
    ...headMetadata,
    message: stringAt(step, "message"),
    rawXml: Boolean(step.raw_xml)
  };
}

function labwareFromRecord(record: Record<string, unknown>, source: string, index: number): LabwareModel {
  const positionExpression = stringAt(record, "position") || stringAt(record, "Position");
  const label =
    stringAt(record, "label") ||
    stringAt(record, "LabwareLable") ||
    stringAt(record, "LabwareLabel") ||
    stringAt(record, "LabwareName") ||
    stringAt(record, "id") ||
    `Labware ${index + 1}`;
  const catalogName =
    stringAt(record, "catalog") ||
    stringAt(record, "catalogName") ||
    stringAt(record, "labware_type") ||
    stringAt(record, "LabwareType") ||
    inferCatalogName(label);
  return createLabware({
    label,
    catalogName,
    source,
    slotIndex: index,
    role: stringAt(record, "role"),
    location: stringAt(record, "location") || stringAt(record, "Location") || stringAt(record, "deck_location"),
    position: numberAt(record, "position") ?? numberAt(record, "Position"),
    positionExpression
  });
}

function labwareFromRawXmlStep(step: Record<string, unknown>, stepIndex: number): LabwareModel[] {
  const rawXml = stringAt(objectAt(step, "parameters"), "raw_xml");
  if (!rawXml) return [];

  try {
    const document = new DOMParser().parseFromString(rawXml, "application/xml");
    if (document.querySelector("parsererror")) return [];
    return Array.from(document.querySelectorAll("AddLabwareDataV1")).map((node, index) =>
      labwareFromRecord(recordFromXscrNode(node), "IR raw XML", stepIndex * 10 + index)
    );
  } catch {
    return [];
  }
}

function recordFromXscrNode(node: Element): Record<string, unknown> {
  const names = [
    "LabwareType",
    "LabwareLable",
    "LabwareLabel",
    "LabwareName",
    "Location",
    "Position",
    "Rotation",
    "HasLid",
    "Barcode"
  ];
  return Object.fromEntries(names.map((name) => [name, textOf(node, name)]).filter(([, value]) => value));
}

function createLabware(input: CreateLabwareInput): LabwareModel {
  const normalizedInput = input.transform ? normalizeFluentLabwareInput(input) : input;
  const catalogName = normalizedInput.catalogName || inferCatalogName(normalizedInput.label);
  const geometry = displayGeometryForLabware(
    resolveLabwareGeometry(catalogName, normalizedInput.label, normalizedInput.meshGuid, normalizedInput.meshName),
    normalizedInput
  );
  const wellGeometry = geometryWithInputWellGeometry(geometry, normalizedInput.wellSites);
  const capacityUl = wellGeometry.maxVolumeUl;
  const color = colorForLabware(catalogName, normalizedInput.label, normalizedInput.role || "");
  const rows = wellGeometry.rows;
  const cols = wellGeometry.cols;
  const wells = wellGeometry.shape === "none" ? [] : applyWellPositions(makeWells(wellGeometry), normalizedInput.wellSites);
  const snapSites = fluentSnapSitesFromInput(normalizedInput.wellSites);
  const position = normalizedInput.position ?? null;
  const positionExpression = normalizedInput.positionExpression?.trim() || (position !== null ? String(position) : "");

  return {
    id: slug(normalizedInput.label),
    label: normalizedInput.label,
    catalogName,
    geometryName: wellGeometry.name,
    geometrySource: wellGeometry.source,
    role: normalizedInput.role || "",
    source: normalizedInput.source,
    location: normalizedInput.location || "",
    position,
    positionExpression: positionExpression || undefined,
    slotIndex: position !== null ? Math.max(0, position - 1) : normalizedInput.slotIndex,
    rows,
    cols,
    wellShape: wellGeometry.shape,
    capacityUl,
    maxVolumeUl: wellGeometry.maxVolumeUl,
    deadVolumeUl: wellGeometry.deadVolumeUl,
    physicalWidthMm: wellGeometry.physicalWidthMm,
    physicalDepthMm: wellGeometry.physicalDepthMm,
    physicalHeightMm: wellGeometry.physicalHeightMm,
    pitchXMm: wellGeometry.pitchXMm,
    pitchYMm: wellGeometry.pitchYMm,
    wellDiameterMm: wellGeometry.wellDiameterMm,
    wellDepthMm: wellGeometry.wellDepthMm,
    color,
    transform: normalizedInput.transform || null,
    wells,
    snapSites,
    hardwareProfile: hardwareProfileFromZeia({
      functionalGroup: normalizedInput.functionalGroup || wellGeometry.functionalGroup,
      nameText: `${catalogName} ${normalizedInput.label}`,
      shape: wellGeometry.shape
    }),
    functionalGroup: normalizedInput.functionalGroup || wellGeometry.functionalGroup,
    hardwareAssetIds: [],
    hardwareAssetNames: [],
    meshGuid: normalizedInput.meshGuid,
    meshName: normalizedInput.meshName,
    meshSourcePath: normalizedInput.meshSourcePath,
    meshPayloadSize: normalizedInput.meshPayloadSize,
    meshDecodedSize: normalizedInput.meshDecodedSize ?? undefined,
    unitScaleToMm: normalizedInput.unitScaleToMm,
    unitScaleSource: normalizedInput.unitScaleSource,
    nativeUnit: normalizedInput.nativeUnit,
    modelAssetPath: normalizedInput.modelAssetPath,
    modelAssetFormat: normalizedInput.modelAssetFormat
  };
}

function fluentSnapSitesFromInput(wellSites: SnapSiteModel[] | undefined): SnapSiteModel[] | undefined {
  const snapSites = (wellSites || []).filter((site) => site.positionMm && isFiniteVector(site.positionMm));
  return snapSites.length ? snapSites : undefined;
}

function normalizeFluentLabwareInput(input: CreateLabwareInput): CreateLabwareInput {
  if (!input.transform) return input;

  const kind = fluentComponentKind(input);
  return {
    ...input,
    catalogName: fluentCatalogNameFor(input, kind),
    role: fluentRoleFor(input.role || "", kind)
  };
}

function fluentCatalogNameFor(input: CreateLabwareInput, kind: FluentComponentKind): string {
  const label = input.label || "Worktable component";
  const catalogName = (input.catalogName || "").trim();
  // Exact catalog/label from ZEIA / worktable only — never invent Wash Station / DiTiWaste / Falcon / CapHolder.
  if (catalogName) return catalogName;
  if (kind === "tip_box" || kind === "tube_rack" || kind === "labware" || kind === "cap_holder") {
    return inferCatalogName(label);
  }
  return label.replace(/\[[^\]]+\]\s*$/, "").trim() || GENERIC_FLUENT_COMPONENT_CATALOG;
}

function displayGeometryForLabware(geometry: ResolvedLabwareGeometry, input: CreateLabwareInput): ResolvedLabwareGeometry {
  if (!input.transform) return geometry;

  const kind = fluentComponentKind(input);
  if (fluentKindCanContainLiquidOrTips(kind)) return geometryWithFluentWellSites(geometry, input);

  const dimension = input.transform.dimensionMm;
  const physicalWidthMm = finiteGeometryDimension(dimension?.x, geometry.physicalWidthMm, 5, 1000);
  const physicalDepthMm = finiteGeometryDimension(dimension?.y, geometry.physicalDepthMm, 5, 1000);
  const physicalHeightMm = finiteGeometryDimension(dimension?.z, geometry.physicalHeightMm, 3, 250);

  return {
    ...geometry,
    name: geometry.name === GENERIC_FLUENT_COMPONENT_CATALOG ? "Fluent Component" : geometry.name,
    physicalWidthMm,
    physicalDepthMm,
    physicalHeightMm,
    rows: 0,
    cols: 0,
    pitchXMm: 0,
    pitchYMm: 0,
    wellDiameterMm: 0,
    wellDepthMm: 0,
    maxVolumeUl: 0,
    deadVolumeUl: 0,
    shape: "none"
  };
}

function geometryWithInputWellGeometry(
  geometry: ResolvedLabwareGeometry,
  wellSites: CreateLabwareInput["wellSites"] | undefined
): ResolvedLabwareGeometry {
  if (geometry.shape === "none" || !wellSites?.length) return geometry;
  const fluentGeometry = wellGeometryFromSites(wellSites);
  if (!fluentGeometry.wellShape && !fluentGeometry.wellDiameterMm && !fluentGeometry.wellDepthMm) return geometry;

  return {
    ...geometry,
    shape: fluentGeometry.wellShape || geometry.shape,
    wellDiameterMm: finiteGeometryDimension(fluentGeometry.wellDiameterMm, geometry.wellDiameterMm, 0.1, 200),
    wellDepthMm: finiteGeometryDimension(fluentGeometry.wellDepthMm, geometry.wellDepthMm, 0.1, 300)
  };
}

function wellGeometryFromSites(wellSites: CreateLabwareInput["wellSites"]): {
  wellShape?: WellShape;
  wellDiameterMm?: number;
  wellDepthMm?: number;
} {
  const first = wellSites?.find((site) => site.wellShape || site.diameterMm || site.depthMm);
  return {
    wellShape: first?.wellShape,
    wellDiameterMm: first?.diameterMm,
    wellDepthMm: first?.depthMm
  };
}

function geometryWithFluentWellSites(geometry: ResolvedLabwareGeometry, input: CreateLabwareInput): ResolvedLabwareGeometry {
  const dimension = input.transform?.dimensionMm;
  const physicalWidthMm = finiteGeometryDimension(dimension?.x, geometry.physicalWidthMm, 5, 1000);
  const physicalDepthMm = finiteGeometryDimension(dimension?.y, geometry.physicalDepthMm, 5, 1000);
  const physicalHeightMm = finiteGeometryDimension(dimension?.z, geometry.physicalHeightMm, 3, 250);
  const dimensionedGeometry = {
    ...geometry,
    physicalWidthMm,
    physicalDepthMm,
    physicalHeightMm
  };

  if (geometry.shape === "none" || !input.wellSites?.length) return dimensionedGeometry;

  const siteCount = input.wellSites.filter((site) => site.positionMm && isFiniteVector(site.positionMm)).length;
  if (!siteCount || siteCount === geometry.rows * geometry.cols) return dimensionedGeometry;

  const grid = fluentWellSiteGrid(input.wellSites, siteCount);
  if (!grid) return dimensionedGeometry;

  return {
    ...dimensionedGeometry,
    name: geometry.source === "inferred" ? `Fluent ${grid.rows}x${grid.cols} Labware` : geometry.name,
    rows: grid.rows,
    cols: grid.cols,
    pitchXMm: grid.pitchXMm || geometry.pitchXMm,
    pitchYMm: grid.pitchYMm || geometry.pitchYMm
  };
}

function fluentWellSiteGrid(wellSites: NonNullable<CreateLabwareInput["wellSites"]>, siteCount: number): {
  rows: number;
  cols: number;
  pitchXMm: number;
  pitchYMm: number;
} | null {
  if (siteCount === 1) return { rows: 1, cols: 1, pitchXMm: 0, pitchYMm: 0 };

  const rows = wellSites.map((site) => positiveIntegerValue(site.row)).filter((value) => value !== null) as number[];
  const cols = wellSites.map((site) => positiveIntegerValue(site.col)).filter((value) => value !== null) as number[];
  if (!rows.length || !cols.length) return null;

  const siteRows = Math.max(...rows) + 1;
  const siteCols = Math.max(...cols) + 1;
  if (siteRows * siteCols !== siteCount) return null;

  const linearAlongFluentY = siteCols === 1 && siteRows > 1;
  return {
    rows: linearAlongFluentY ? 1 : siteRows,
    cols: linearAlongFluentY ? siteRows : siteCols,
    pitchXMm: medianAdjacentSpacingMm(wellSites, linearAlongFluentY ? "y" : "x"),
    pitchYMm: linearAlongFluentY ? 0 : medianAdjacentSpacingMm(wellSites, "y")
  };
}

function medianAdjacentSpacingMm(wellSites: NonNullable<CreateLabwareInput["wellSites"]>, axis: keyof Vector3Model): number {
  const positions = wellSites
    .map((site) => site.positionMm?.[axis])
    .filter((value): value is number => Number.isFinite(value))
    .sort((a, b) => a - b);
  const spacings: number[] = [];
  for (let index = 1; index < positions.length; index += 1) {
    const spacing = Math.abs(positions[index] - positions[index - 1]);
    if (spacing > 0.001) spacings.push(spacing);
  }
  if (!spacings.length) return 0;
  spacings.sort((a, b) => a - b);
  return spacings[Math.floor(spacings.length / 2)];
}

function positiveIntegerValue(value: unknown): number | null {
  const integer = Math.floor(Number(value));
  return Number.isFinite(integer) && integer >= 0 ? integer : null;
}

function finiteGeometryDimension(value: unknown, fallback: number, min: number, max: number): number {
  const numberValue = Number(value);
  const fallbackValue = Number.isFinite(fallback) && fallback > 0 ? fallback : min;
  if (!Number.isFinite(numberValue) || numberValue <= 0) return fallbackValue;
  return Math.max(min, Math.min(max, numberValue));
}

function fluentKindCanContainLiquidOrTips(kind: FluentComponentKind): boolean {
  return kind === "labware" || kind === "tube_rack" || kind === "tip_box" || kind === "reservoir" || kind === "wash" || kind === "cap_holder";
}

function fluentRoleFor(role: string, kind: FluentComponentKind): string {
  const trimmed = role.trim();
  if (trimmed.toLowerCase().startsWith("fluent:")) return trimmed;
  return trimmed ? `fluent:${kind}:${trimmed}` : `fluent:${kind}`;
}

function fluentComponentKind(input: CreateLabwareInput): FluentComponentKind {
  if (!input.transform) return "labware";

  // Prefer already-classified Fluent role / ZEIA FunctionalGroup over name keywords.
  const roleText = canonicalText(input.role || "");
  if (roleText === "tube-rack" || roleText.includes("tube-rack") || roleText.includes("tube rack")) return "tube_rack";
  if (roleText === "tip-box" || roleText.includes("tip-box") || roleText.includes("tip box")) return "tip_box";
  if (roleText === "waste") return "waste";
  if (roleText === "carrier") return "carrier";
  if (roleText === "nest") return "nest";
  if (roleText === "adapter") return "adapter";
  if (roleText === "device") return "device";
  if (roleText === "deck" || roleText === "structural") return "structural";
  if (roleText === "labware") return "labware";

  const fromFg = hardwareProfileFromZeia({
    functionalGroup: input.functionalGroup,
    nameText: `${input.catalogName || ""} ${input.label}`
  });
  if (fromFg === "tube-holder") return "tube_rack";
  if (fromFg === "tip-box") return "tip_box";
  if (fromFg === "reservoir") return "reservoir";
  if (fromFg === "waste" || fromFg === "wash-station") return fromFg === "wash-station" ? "wash" : "waste";
  if (fromFg === "carrier") return "carrier";
  if (fromFg === "nest") return "nest";
  if (fromFg === "adapter") return "adapter";
  if (fromFg === "device") return "device";
  if (fromFg === "deck" || fromFg === "structural") return "structural";
  if (fromFg === "plate" || fromFg === "generic") return "labware";

  // Exact tube phrases only — never falcon/conical/filter/DWP invent.
  const text = canonicalText(`${input.catalogName || ""} ${input.label}`);
  if (textIncludesAny(text, ["tube runner", "tuberunner", "tube holder", "tubeholder"])) return "tube_rack";

  return "unknown_component";
}

function textIncludesAny(text: string, needles: string[]): boolean {
  return needles.some((needle) => text.includes(needle));
}

function makeWells(geometry: ResolvedLabwareGeometry): WellModel[] {
  const wells: WellModel[] = [];
  const rows = geometry.rows;
  const cols = geometry.cols;
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const id = `${String.fromCharCode(65 + row)}${col + 1}`;
      wells.push({
        id,
        row,
        col,
        volumeUl: 0,
        capacityUl: geometry.maxVolumeUl,
        maxVolumeUl: geometry.maxVolumeUl,
        deadVolumeUl: geometry.deadVolumeUl,
        diameterMm: geometry.wellDiameterMm,
        depthMm: geometry.wellDepthMm,
        reagent: "",
        layers: []
      });
    }
  }
  return wells;
}

function applyWellPositions(
  wells: WellModel[],
  wellSites: CreateLabwareInput["wellSites"] | undefined
): WellModel[] {
  if (!wells.length || !wellSites?.length) return wells;
  const usableSites = wellSites.filter((site) => site.positionMm && isFiniteVector(site.positionMm));
  if (usableSites.length !== wells.length) return wells;

  return wells.map((well, index) => ({
    ...well,
    positionMm: usableSites[index].positionMm,
    positionSource: usableSites[index].source || "fluent-pipettable",
    diameterMm: finiteGeometryDimension(usableSites[index].diameterMm, well.diameterMm, 0.1, 200),
    depthMm: finiteGeometryDimension(usableSites[index].depthMm, well.depthMm, 0.1, 300)
  }));
}

function enrichLabwareWithDefinitions(labware: LabwareModel[], definitions: LabwareDefinitionModel[]): LabwareModel[] {
  if (!definitions.length) return labware;
  return labware.map((item) => {
    if (!item.wells.length || item.wells.some((well) => well.positionMm)) return item;
    const definition = bestLabwareDefinitionFor(item, definitions);
    if (!definition) return item;

    const wells = applyWellPositions(item.wells, definition.wellSites);
    if (!wells.some((well) => well.positionMm)) return item;

    const grid = fluentWellSiteGrid(definition.wellSites, definition.wellSites.length);
    return {
      ...item,
      geometryName: definition.componentName || item.geometryName,
      physicalWidthMm: finiteGeometryDimension(definition.dimensionMm?.x, item.physicalWidthMm, 5, 1000),
      physicalDepthMm: finiteGeometryDimension(definition.dimensionMm?.y, item.physicalDepthMm, 5, 1000),
      physicalHeightMm: finiteGeometryDimension(definition.dimensionMm?.z, item.physicalHeightMm, 3, 250),
      wellShape: definition.wellShape || item.wellShape,
      wellDiameterMm: finiteGeometryDimension(definition.wellDiameterMm, item.wellDiameterMm, 0.1, 200),
      wellDepthMm: finiteGeometryDimension(definition.wellDepthMm, item.wellDepthMm, 0.1, 300),
      rows: grid?.rows || item.rows,
      cols: grid?.cols || item.cols,
      pitchXMm: grid?.pitchXMm || item.pitchXMm,
      pitchYMm: grid?.pitchYMm || item.pitchYMm,
      wells
    };
  });
}

function bestLabwareDefinitionFor(labware: LabwareModel, definitions: LabwareDefinitionModel[]): LabwareDefinitionModel | null {
  const scored = definitions
    .filter((definition) => definition.wellSites.length === labware.wells.length)
    .map((definition) => ({ definition, score: labwareDefinitionScore(labware, definition) }))
    .filter((candidate) => candidate.score >= 120)
    .sort((a, b) => b.score - a.score || a.definition.componentName.localeCompare(b.definition.componentName));
  return scored[0]?.definition || null;
}

function labwareDefinitionScore(labware: LabwareModel, definition: LabwareDefinitionModel): number {
  const labwareText = canonicalText(`${labware.label} ${labware.catalogName} ${labware.geometryName} ${labware.hardwareProfile}`);
  const definitionText = canonicalText(`${definition.catalogName} ${definition.componentName} ${definition.role}`);
  let score = 0;

  if (canonicalText(definition.catalogName) === canonicalText(labware.catalogName)) score += 180;
  if (canonicalText(definition.componentName) === canonicalText(labware.catalogName)) score += 150;
  if (canonicalText(definition.componentName) === canonicalText(labware.geometryName)) score += 120;
  const definitionProfile = hardwareProfileFromZeia({
    functionalGroup: definition.functionalGroup || labware.functionalGroup,
    nameText: `${definition.catalogName} ${definition.componentName}`
  });
  if (definitionProfile === labware.hardwareProfile) score += 85;
  if (labware.rows && labware.cols && definition.wellSites.length === labware.rows * labware.cols) score += 35;

  if (labwareText.includes("filter")) {
    score += definitionText.includes("filter") ? 520 : -500;
  }
  if ((labwareText.includes("collectiondwp") || labwareText.includes("24 dwp")) && definitionText.includes("filter")) score -= 140;
  if ((labwareText.includes("collectiondwp") || labwareText.includes("24 dwp")) && definitionText.includes("24 dwp")) score += 180;
  if (labwareText.includes("tube runner") && definitionText.includes("tube runner")) score += 140;
  if (labwareText.includes("tube runner") && definitionText.includes("tube runner")) score += 140;

  return score;
}

function isFiniteVector(vector: Vector3Model): boolean {
  return Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z);
}

function mergeLabware(labware: LabwareModel[]): LabwareModel[] {
  const byLabel = new Map<string, LabwareModel>();
  labware.forEach((item) => {
    const key = item.label.toLowerCase();
    const existing = byLabel.get(key);
    if (!existing) {
      byLabel.set(key, item);
      return;
    }

    const geometrySource = preferredGeometryForMerge(existing, item);
    const mergedWells = mergeWellsForGeometry(geometrySource, existing, item);
    const position = existing.position ?? item.position;
    const positionExpression = existing.positionExpression || item.positionExpression || (position !== null ? String(position) : undefined);

    byLabel.set(key, {
      ...existing,
      catalogName: geometrySource.catalogName || existing.catalogName || item.catalogName,
      geometryName: geometrySource.geometryName,
      geometrySource: geometrySource.geometrySource,
      source: Array.from(new Set([existing.source, item.source].filter(Boolean))).join(" + "),
      role: firstString([existing.role, item.role]),
      location: existing.location || item.location,
      position,
      positionExpression,
      slotIndex: position !== null ? Math.max(0, position - 1) : existing.slotIndex,
      transform: existing.transform || item.transform,
      rows: geometrySource.rows,
      cols: geometrySource.cols,
      wellShape: geometrySource.wellShape,
      capacityUl: geometrySource.capacityUl,
      maxVolumeUl: geometrySource.maxVolumeUl,
      deadVolumeUl: geometrySource.deadVolumeUl,
      physicalWidthMm: geometrySource.physicalWidthMm,
      physicalDepthMm: geometrySource.physicalDepthMm,
      physicalHeightMm: geometrySource.physicalHeightMm,
      pitchXMm: geometrySource.pitchXMm,
      pitchYMm: geometrySource.pitchYMm,
      wellDiameterMm: geometrySource.wellDiameterMm,
      wellDepthMm: geometrySource.wellDepthMm,
      color: geometrySource.color || existing.color,
      hardwareProfile: firstSpecificProfile([existing.hardwareProfile, item.hardwareProfile]) || geometrySource.hardwareProfile,
      functionalGroup: existing.functionalGroup || item.functionalGroup,
      hardwareAssetIds: Array.from(new Set([...existing.hardwareAssetIds, ...item.hardwareAssetIds])),
      hardwareAssetNames: Array.from(new Set([...existing.hardwareAssetNames, ...item.hardwareAssetNames])),
      meshGuid: existing.meshGuid || item.meshGuid,
      meshName: existing.meshName || item.meshName,
      meshSourcePath: existing.meshSourcePath || item.meshSourcePath,
      meshPayloadSize: existing.meshPayloadSize ?? item.meshPayloadSize,
      meshDecodedSize: existing.meshDecodedSize ?? item.meshDecodedSize,
      unitScaleToMm: existing.unitScaleToMm ?? item.unitScaleToMm,
      unitScaleSource: existing.unitScaleSource || item.unitScaleSource,
      nativeUnit: existing.nativeUnit || item.nativeUnit,
      modelAssetPath: existing.modelAssetPath || item.modelAssetPath,
      modelAssetFormat: existing.modelAssetFormat || item.modelAssetFormat,
      wells: mergedWells
    });
  });

  return Array.from(byLabel.values()).sort((a, b) => a.slotIndex - b.slotIndex || a.label.localeCompare(b.label));
}

function preferredGeometryForMerge(existing: LabwareModel, incoming: LabwareModel): LabwareModel {
  const existingAffinity = labwareGeometryAffinity(existing);
  const incomingAffinity = labwareGeometryAffinity(incoming);
  if (incomingAffinity > existingAffinity) return incoming;
  if (existingAffinity > incomingAffinity) return existing;

  const existingExactPositions = exactWellPositionCount(existing);
  const incomingExactPositions = exactWellPositionCount(incoming);
  if (incomingExactPositions > existingExactPositions) return incoming;
  if (existingExactPositions > incomingExactPositions) return existing;

  if (isDefaultPlateFallback(existing) && !isDefaultPlateFallback(incoming)) return incoming;
  if (isDefaultPlateFallback(incoming) && !isDefaultPlateFallback(existing)) return existing;

  if (existing.geometrySource === "catalog" && incoming.geometrySource !== "catalog") return existing;
  if (incoming.geometrySource === "catalog" && existing.geometrySource !== "catalog") return incoming;

  if (!existing.transform && incoming.transform && incoming.wellShape === "none") return incoming;
  if (existing.transform && !incoming.transform && existing.wellShape === "none") return existing;

  return existing;
}

function labwareGeometryAffinity(item: LabwareModel): number {
  const targetText = canonicalText(`${item.label} ${item.catalogName}`);
  const geometryText = canonicalText(`${item.geometryName} ${item.catalogName}`);
  let score = 0;
  if (targetText.includes("filter")) score += geometryText.includes("filter") ? 120 : -80;
  if (targetText.includes("collectiondwp")) score += geometryText.includes("24 dwp") || geometryText.includes("dwp") ? 90 : 0;
  if (targetText.includes("tube runner")) {
    score += geometryText.includes("tube runner") ? 60 : 0;
  }
  return score;
}

function exactWellPositionCount(item: LabwareModel): number {
  return item.wells.filter((well) => well.positionMm && well.positionSource !== "catalog").length;
}

function isDefaultPlateFallback(item: LabwareModel): boolean {
  const text = canonicalText(`${item.label} ${item.catalogName} ${item.geometryName}`);
  const explicitPlateText = textIncludesAny(text, ["96 well", "96well", "well plate", "microplate", "mca96"]);
  return item.geometrySource === "inferred" && item.geometryName === "96 Well Flat" && item.wellShape !== "none" && !explicitPlateText;
}

function mergeWellsForGeometry(geometrySource: LabwareModel, existing: LabwareModel, incoming: LabwareModel): WellModel[] {
  if (geometrySource.wellShape === "none") return [];

  const baseWells = geometrySource === incoming ? incoming.wells : existing.wells;
  const otherWells = geometrySource === incoming ? existing.wells : incoming.wells;
  if (!baseWells.length) return [];

  return baseWells.map((well) => {
    const other = otherWells.find((candidate) => candidate.id === well.id);
    if (!other) return well;
    const positionedWell = mergeWellPosition(well, other);
    if (other.volumeUl <= well.volumeUl && !other.layers.length && !other.reagent) return positionedWell;
    return {
      ...positionedWell,
      volumeUl: Math.max(well.volumeUl, other.volumeUl),
      reagent: other.reagent || well.reagent,
      layers: other.layers.length ? other.layers : well.layers
    };
  });
}

function mergeWellPosition(base: WellModel, other: WellModel): WellModel {
  if (base.positionMm) return base;
  if (!other.positionMm) return base;
  return {
    ...base,
    positionMm: other.positionMm,
    positionSource: other.positionSource
  };
}

function choosePrimaryCommands(
  artifactBundles: ParsedBundle[],
  artifacts: SourceArtifact[],
  bundles: ParsedBundle[],
  primaryScriptArtifactKey?: string
): CommandModel[] {
  if (primaryScriptArtifactKey) {
    const selected = commandsForSelectedArtifact(artifactBundles, artifacts, bundles, primaryScriptArtifactKey);
    if (selected?.length) return selected;
  }

  const sourcePriority = ["IR", "XSCR", "GWL", "simulation"];
  const xscrTransferCommands = transferCommandsFromBundles(bundles);
  const commandsForSource = (source: string): CommandModel[] =>
    bundles.flatMap((bundle) => bundle.commands).filter((command) => command.source === source);
  const hasConcreteLiquid = (commands: CommandModel[]): boolean =>
    commands.some((command) => command.family === "liquid" && command.targetLabware);

  for (const source of sourcePriority) {
    const commands = commandsForSource(source);
    if (!commands.length) continue;
    if (source === "IR") return enrichCommands([...commands, ...xscrTransferCommands], bundles);
    if (source === "XSCR") {
      // XSCR drives the command queue, but a script can carry only setup /
      // transfer steps while the actual aspirate/dispense work lives in the
      // companion .gwl worklist. When XSCR has no concrete liquid handling,
      // splice in the GWL liquid commands rather than dropping them entirely.
      if (hasConcreteLiquid(commands)) return enrichCommands(commands, bundles);
      const gwlLiquidCommands = commandsForSource("GWL").filter((command) => command.family === "liquid");
      if (gwlLiquidCommands.length) return enrichCommands([...commands, ...gwlLiquidCommands], bundles);
      return enrichCommands(commands, bundles);
    }
    if (hasConcreteLiquid(commands) || source === "simulation") return enrichCommands(commands, bundles);
  }
  return [];
}

function commandsForSelectedArtifact(
  artifactBundles: ParsedBundle[],
  artifacts: SourceArtifact[],
  bundles: ParsedBundle[],
  primaryScriptArtifactKey: string
): CommandModel[] {
  const artifactIndex = artifacts.findIndex((artifact) => artifactSelectionKey(artifact) === primaryScriptArtifactKey);
  if (artifactIndex < 0) return [];
  const bundle = artifactBundles[artifactIndex];
  const commands = bundle.commands;
  if (!commands.length) return [];

  const source = commands[0]?.source || sourceLabelForArtifactKind(artifacts[artifactIndex].kind);
  const xscrTransferCommands = transferCommandsFromBundles(bundles);
  const commandsForSource = (label: string): CommandModel[] =>
    bundles.flatMap((entry) => entry.commands).filter((command) => command.source === label);
  const hasConcreteLiquid = (candidate: CommandModel[]): boolean =>
    candidate.some((command) => command.family === "liquid" && command.targetLabware);

  if (source === "IR") return enrichCommands([...commands, ...xscrTransferCommands], bundles);
  if (source === "XSCR") {
    if (hasConcreteLiquid(commands)) return enrichCommands(commands, bundles);
    const gwlLiquidCommands = commandsForSource("GWL").filter((command) => command.family === "liquid");
    if (gwlLiquidCommands.length) return enrichCommands([...commands, ...gwlLiquidCommands], bundles);
    return enrichCommands(commands, bundles);
  }
  if (hasConcreteLiquid(commands) || source === "simulation") return enrichCommands(commands, bundles);
  return enrichCommands(commands, bundles);
}

function sourceLabelForArtifactKind(kind: SourceArtifact["kind"]): string {
  if (kind === "protocol-ir") return "IR";
  if (kind === "xscr") return "XSCR";
  if (kind === "gwl") return "GWL";
  if (kind === "simulation") return "simulation";
  return "unknown";
}

function transferCommandsFromBundles(bundles: ParsedBundle[]): CommandModel[] {
  const byKey = new Map<string, CommandModel>();
  bundles
    .flatMap((bundle) => bundle.commands)
    .filter((command) => command.source === "XSCR" && command.labwareTransfer)
    .forEach((command) => {
      const transfer = command.labwareTransfer as LabwareTransferModel;
      const key = [command.commandId, command.message, transfer.labware, transfer.location, transfer.site].join("|");
      if (!byKey.has(key)) byKey.set(key, command);
    });
  return Array.from(byKey.values()).sort((a, b) => a.index - b.index || a.id.localeCompare(b.id));
}

function enrichCommands(commands: CommandModel[], bundles: ParsedBundle[]): CommandModel[] {
  const simulation = bundles.flatMap((bundle) => bundle.commands).filter((command) => command.source === "simulation");
  return commands
    .map((command, index) => {
      const sim = simulation[index];
      return {
        ...command,
        index,
        effect: command.effect || sim?.effect || effectForOperation(command.operation)
      };
    })
    .sort((a, b) => a.index - b.index);
}

function fallbackCommands(labware: LabwareModel[]): CommandModel[] {
  return labware.map((item, index) => ({
    id: `fallback-${item.id}`,
    index,
    source: item.source || "derived",
    operation: "load_labware",
    family: "labware",
    name: "Load Labware",
    group: "Derived Deck",
    commandId: "load_labware",
    effect: "labware_movement",
    targetLabware: item.label,
    wells: [],
    volumeUl: null,
    liquidClass: "",
    ...emptyHeadMetadata(),
    message: item.catalogName,
    rawXml: false
  }));
}

function mergeHardware(hardwareParts: Partial<HardwareContext>[]): HardwareContext {
  return hardwareParts.reduce<HardwareContext>(
    (accumulator, item) => ({
      assets: accumulator.assets + (item.assets || 0),
      packagedAssets: accumulator.packagedAssets + (item.packagedAssets || 0),
      connectors: accumulator.connectors + (item.connectors || 0),
      pins: accumulator.pins + (item.pins || 0),
      missingAssets: Array.from(new Set([...accumulator.missingAssets, ...(item.missingAssets || [])])),
      notes: Array.from(new Set([...accumulator.notes, ...(item.notes || [])])),
      assetDetails: mergeHardwareAssetDetails([...accumulator.assetDetails, ...(item.assetDetails || [])]),
      worktableMeshes: mergeWorktableMeshes([...(accumulator.worktableMeshes || []), ...(item.worktableMeshes || [])]),
      modelAssets: mergeModelAssets([...(accumulator.modelAssets || []), ...(item.modelAssets || [])])
    }),
    { ...EMPTY_HARDWARE }
  );
}


function mergeWorktableMeshes(meshes: WorktableMeshModel[]): WorktableMeshModel[] {
  const byKey = new Map<string, WorktableMeshModel>();
  meshes.forEach((mesh) => {
    const key = mesh.guid || mesh.sourcePath || mesh.id;
    if (!key) return;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, mesh);
      return;
    }

    byKey.set(key, {
      ...existing,
      name: existing.name || mesh.name,
      objectName: existing.objectName || mesh.objectName,
      source: existing.source || mesh.source,
      sourcePath: existing.sourcePath || mesh.sourcePath,
      artifactId: existing.artifactId || mesh.artifactId,
      payloadSize: existing.payloadSize || mesh.payloadSize,
      decodedSize: existing.decodedSize ?? mesh.decodedSize,
      base64Length: existing.base64Length ?? mesh.base64Length,
      payloadEncoding: existing.payloadEncoding || mesh.payloadEncoding,
      archiveFormat: existing.archiveFormat || mesh.archiveFormat,
      convertedAssetPath: existing.convertedAssetPath || mesh.convertedAssetPath,
      unitScaleToMm: existing.unitScaleToMm ?? mesh.unitScaleToMm,
      unitScaleSource: existing.unitScaleSource || mesh.unitScaleSource,
      nativeUnit: existing.nativeUnit || mesh.nativeUnit,
      modelAssetPath: existing.modelAssetPath || mesh.modelAssetPath,
      modelAssetFormat: existing.modelAssetFormat || mesh.modelAssetFormat,
      references: Array.from(new Set([...existing.references, ...mesh.references].filter(Boolean))),
      notes: Array.from(new Set([...existing.notes, ...mesh.notes].filter(Boolean)))
    });
  });
  return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name) || a.guid.localeCompare(b.guid));
}

function mergeModelAssets(assets: ModelAssetModel[]): ModelAssetModel[] {
  const byKey = new Map<string, ModelAssetModel>();
  assets.forEach((asset) => {
    const key = asset.path || asset.meshGuid || asset.id;
    if (!key) return;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, asset);
      return;
    }

    byKey.set(key, {
      ...existing,
      name: existing.name || asset.name,
      format: existing.format !== "unknown" ? existing.format : asset.format,
      source: existing.source || asset.source,
      meshGuid: existing.meshGuid || asset.meshGuid,
      meshName: existing.meshName || asset.meshName,
      unitScaleToMm: existing.unitScaleToMm ?? asset.unitScaleToMm,
      unitScaleSource: existing.unitScaleSource || asset.unitScaleSource,
      nativeUnit: existing.nativeUnit || asset.nativeUnit,
      componentRole: existing.componentRole || asset.componentRole,
      hardwareProfile: existing.hardwareProfile || asset.hardwareProfile,
      transform: existing.transform || asset.transform,
      references: Array.from(new Set([...existing.references, ...asset.references].filter(Boolean)))
    });
  });
  return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name) || a.path.localeCompare(b.path));
}

function mergeHardwareAssetDetails(assets: HardwareAssetModel[]): HardwareAssetModel[] {
  const byKey = new Map<string, HardwareAssetModel>();
  assets.forEach((asset) => {
    const key = hardwareAssetKey(asset);
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, asset);
      return;
    }

    byKey.set(key, {
      ...existing,
      name: existing.name || asset.name,
      status: existing.status === "asset_packaged" ? existing.status : asset.status || existing.status,
      packaged: existing.packaged || asset.packaged,
      manualVerificationRequired: existing.manualVerificationRequired || asset.manualVerificationRequired,
      bundlePath: existing.bundlePath || asset.bundlePath,
      sourcePath: existing.sourcePath || asset.sourcePath,
      entry: existing.entry || asset.entry,
      imageSrc: existing.imageSrc || asset.imageSrc,
      references: Array.from(new Set([...existing.references, ...asset.references].filter(Boolean))),
      profile: existing.profile !== "generic" ? existing.profile : asset.profile,
      componentRole: existing.componentRole || asset.componentRole,
      meshGuid: existing.meshGuid || asset.meshGuid,
      meshName: existing.meshName || asset.meshName,
      meshSourcePath: existing.meshSourcePath || asset.meshSourcePath,
      meshPayloadSize: existing.meshPayloadSize ?? asset.meshPayloadSize,
      meshDecodedSize: existing.meshDecodedSize ?? asset.meshDecodedSize,
      unitScaleToMm: existing.unitScaleToMm ?? asset.unitScaleToMm,
      unitScaleSource: existing.unitScaleSource || asset.unitScaleSource,
      nativeUnit: existing.nativeUnit || asset.nativeUnit,
      modelAssetPath: existing.modelAssetPath || asset.modelAssetPath,
      modelAssetFormat: existing.modelAssetFormat || asset.modelAssetFormat,
      modelScale: existing.modelScale || asset.modelScale,
      modelRotationDeg: existing.modelRotationDeg || asset.modelRotationDeg,
      modelOffsetMm: existing.modelOffsetMm || asset.modelOffsetMm,
      modelBoundingBoxMm: existing.modelBoundingBoxMm || asset.modelBoundingBoxMm,
      matchedLabware: Array.from(new Set([...existing.matchedLabware, ...asset.matchedLabware]))
    });
  });
  return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name));
}

function linkHardwareAssetsToLabware(labware: LabwareModel[], assets: HardwareAssetModel[]): LabwareModel[] {
  if (!assets.length) return labware;
  return labware.map((item) => {
    const matches = assets
      .map((asset) => ({ asset, score: scoreAssetForLabware(asset, item) }))
      .filter((match) => match.score >= 50)
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
      .map((match) => match.asset);
    const profile =
      firstSpecificProfile(matches.map((asset) => asset.profile)) ||
      hardwareProfileFromZeia({
        functionalGroup: item.functionalGroup,
        nameText: `${item.catalogName} ${item.label}`,
        shape: item.wellShape
      });
    return {
      ...item,
      hardwareProfile: profile,
      hardwareAssetIds: Array.from(new Set([...item.hardwareAssetIds, ...matches.map((asset) => asset.id)])),
      hardwareAssetNames: Array.from(new Set([...item.hardwareAssetNames, ...matches.map((asset) => asset.name)]))
    };
  });
}

function linkLabwareToHardware(hardware: HardwareContext, labware: LabwareModel[]): HardwareContext {
  return {
    ...hardware,
    assetDetails: hardware.assetDetails.map((asset) => ({
      ...asset,
      matchedLabware: labware.filter((item) => item.hardwareAssetIds.includes(asset.id)).map((item) => item.label)
    }))
  };
}

function scoreAssetForLabware(asset: HardwareAssetModel, labware: LabwareModel): number {
  const assetText = canonicalText([asset.name, asset.bundlePath, asset.entry, asset.sourcePath, asset.profile, ...asset.references].join(" "));
  const labwareText = canonicalText(`${labware.label} ${labware.catalogName} ${labware.role}`);

  const labwareIsFilter = labwareText.includes("filter");
  const assetIsFilter = assetText.includes("filter");
  const labwareIsDwp = labwareText.includes("dwp") || labwareText.includes("deepwell") || labwareText.includes("deep well");
  const assetIsDwp = assetText.includes("24dwp") || assetText.includes("dwp");
  const labwareIsTube = labwareText.includes("tube holder") || labwareText.includes("tube runner");
  const assetIsTube = assetText.includes("tube holder") || assetText.includes("tube runner");
  const labwareIsCap = labwareText.includes("cap");
  const assetIsCap = assetText.includes("cap");
  const labwareIsTip = labwareText.includes("tip");
  const assetIsTip = assetText.includes("tip");

  if (labwareIsFilter) return assetIsFilter ? 120 : assetIsDwp ? 18 : 0;
  if (labwareIsDwp) return assetIsFilter ? 18 : assetIsDwp ? 110 : 0;
  if (labwareIsCap) return assetIsCap ? 105 : assetIsTube ? 20 : 0;
  if (labwareIsTube) return assetIsTube ? 96 : assetIsCap ? 12 : 0;
  if (labwareIsTip) return assetIsTip ? 92 : 0;

  const labwareTokens = new Set(labwareText.split(" ").filter((token) => token.length >= 4));
  const overlap = assetText.split(" ").filter((token) => labwareTokens.has(token)).length;
  return overlap >= 2 ? overlap * 10 : 0;
}

function firstSpecificProfile(profiles: HardwareProfile[]): HardwareProfile | null {
  return profiles.find((profile) => profile !== "generic") || null;
}

function hardwareAssetKey(asset: HardwareAssetModel): string {
  return canonicalAssetName(asset.name) || canonicalAssetName(asset.bundlePath) || canonicalAssetName(asset.entry) || asset.id;
}

function basename(pathOrName: string): string {
  return pathOrName.split(/[\\/]/).filter(Boolean).pop() || pathOrName;
}

function displayAssetName(pathOrName: string): string {
  const baseName = basename(pathOrName);
  return baseName.replace(/^asset_\d+_/i, "") || pathOrName;
}

function canonicalAssetName(pathOrName: string): string {
  const baseName = displayAssetName(pathOrName).replace(/\.[a-z0-9]+$/i, "");
  return baseName.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function canonicalText(text: string): string {
  return text
    .toLowerCase()
    .replace(/([a-z])([0-9])/g, "$1 $2")
    .replace(/([0-9])([a-z])/g, "$1 $2")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function mergeRepairs(parts: Partial<WorktableRepairModel>[]): WorktableRepairModel {
  const issues = dedupeById(parts.flatMap((part) => part.issues || []));
  const validationChecks = dedupeById(parts.flatMap((part) => part.validationChecks || []));
  const validationGates = dedupeById(parts.flatMap((part) => part.validationGates || []));
  const aliasRepairs = Array.from(
    new Map(
      parts
        .flatMap((part) => part.aliasRepairs || [])
        .map((repair) => [`${repair.kind}:${repair.oldName}:${repair.newName}:${repair.source}`, repair])
    ).values()
  );
  const manualSteps = Array.from(new Set(parts.flatMap((part) => part.manualSteps || [])));
  const sourceCounts = mergeCounts(parts.map((part) => part.sourceCounts || {}));
  const protocolCounts = mergeCounts(parts.map((part) => part.protocolCounts || {}));
  const summary = summaryFromIssues(issues, {});

  return {
    ...EMPTY_REPAIRS,
    sourceWorktable: firstString(parts.map((part) => part.sourceWorktable)),
    protocolWorktable: firstString(parts.map((part) => part.protocolWorktable)),
    sourceCounts,
    protocolCounts,
    issues,
    validationChecks,
    validationGates,
    aliasRepairs,
    manualSteps,
    summary
  };
}

function issueFromPatchOperation(operation: Record<string, unknown>, index: number): WorktableIssue {
  const category = stringAt(operation, "category");
  const action = stringAt(operation, "action");
  const target = objectAt(operation, "target");
  const source = objectAt(operation, "source");
  const kind = issueKindFor(category, action);
  const label =
    stringAt(target, "label") ||
    stringAt(target, "name") ||
    stringAt(source, "label") ||
    stringAt(source, "name") ||
    titleCase(action || category || "issue");
  const sourceValue = deckLocation(source) || stringAt(source, "name") || stringAt(source, "guid");
  const targetValue = deckLocation(target) || stringAt(target, "name") || stringAt(target, "guid");
  return makeIssue({
    id: stringAt(operation, "id") || `patch-operation-${index}`,
    kind,
    severity: severityFromValue(stringAt(operation, "severity")),
    status: stringAt(operation, "status") || "unknown",
    label,
    detail: stringAt(operation, "reason") || titleCase(action),
    sourceValue,
    targetValue,
    suggestion: suggestionForIssue(kind, action, label, targetValue),
    diffPath: stringAt(operation, "diff_path") || `/operations/${index}`
  });
}

function appendRequirementIssues(
  issues: WorktableIssue[],
  diff: Record<string, unknown>,
  key: string,
  kind: WorktableIssueKind,
  suggestion: string
): void {
  arrayAt(diff, key).forEach((item, index) => {
    const status = stringAt(item, "status") || "required";
    issues.push(
      makeIssue({
        id: `${key}-${index}`,
        kind,
        severity: statusSeverity(status),
        status,
        label: stringAt(item, "name") || stringAt(item, "label") || titleCase(key),
        detail: status === "available" ? "Available in source context" : "Required by protocol",
        targetValue: deckLocation(item),
        suggestion: status === "available" ? "No action needed." : suggestion,
        diffPath: `/${key}/${index}`
      })
    );
  });
}

function makeIssue(input: Partial<WorktableIssue> & Pick<WorktableIssue, "id" | "kind" | "label">): WorktableIssue {
  return {
    id: input.id,
    kind: input.kind,
    severity: input.severity || "info",
    status: input.status || "unknown",
    label: input.label,
    detail: input.detail || "",
    sourceValue: input.sourceValue || "",
    targetValue: input.targetValue || "",
    suggestion: input.suggestion || "",
    diffPath: input.diffPath || ""
  };
}

function issueKindFor(category: string, action: string): WorktableIssueKind {
  const text = `${category} ${action}`.toLowerCase();
  if (text.includes("liquid")) return "liquid_class";
  if (text.includes("device")) return "device_alias";
  if (text.includes("worklist")) return "worklist";
  if (text.includes("carrier")) return "carrier";
  if (text.includes("tip")) return "tip_box";
  if (text.includes("worktable")) return "worktable";
  if (text.includes("move")) return "changed_position";
  if (text.includes("labware")) return "missing_labware";
  return "warning";
}

function validationIssueKind(id: string): WorktableIssueKind {
  const lower = id.toLowerCase();
  if (lower.includes("worklist")) return "worklist";
  if (lower.includes("liquid")) return "liquid_class";
  if (lower.includes("repair")) return "warning";
  if (lower.includes("worktable")) return "worktable";
  return "validation";
}

function suggestionForIssue(kind: WorktableIssueKind, action: string, label: string, targetValue: string): string {
  if (kind === "changed_position") return `Move ${label} to ${targetValue || "the required deck position"}.`;
  if (kind === "missing_labware") return `Add ${label} to the worktable or configure a labware alias.`;
  if (kind === "liquid_class") return `Confirm ${label} exists or add a liquid-class alias.`;
  if (kind === "device_alias") return `Confirm ${label} exists or add a device-alias mapping.`;
  if (kind === "worklist") return `Check that ${label} is present and referenced by the generated protocol.`;
  if (kind === "worktable") return action.includes("change") ? "Review the selected source and protocol worktable GUIDs." : "Review worktable setup.";
  return "Review before FluentControl import.";
}

function severityFromValue(value: string): WorktableIssue["severity"] {
  const lower = value.toLowerCase();
  if (lower.includes("block") || lower.includes("missing") || lower.includes("failed")) return "blocking";
  if (lower.includes("review") || lower.includes("user") || lower.includes("warning") || lower.includes("changed")) return "needs_review";
  if (lower.includes("safe") || lower.includes("available") || lower.includes("passed") || lower.includes("ready")) return "safe";
  return "info";
}

function statusSeverity(status: string): WorktableIssue["severity"] {
  return severityFromValue(status);
}

function summaryFromIssues(issues: WorktableIssue[], raw: Record<string, unknown>): WorktableRepairModel["summary"] {
  const rawSummary = objectAt(raw, "summary");
  const counts = {
    blocking: numberAt(rawSummary, "blocking") ?? issues.filter((issue) => issue.severity === "blocking").length,
    needsReview:
      numberAt(rawSummary, "needs_review") ??
      numberAt(rawSummary, "needsReview") ??
      issues.filter((issue) => issue.severity === "needs_review").length,
    safe: numberAt(rawSummary, "safe") ?? issues.filter((issue) => issue.severity === "safe").length,
    info: numberAt(rawSummary, "info") ?? issues.filter((issue) => issue.severity === "info").length
  };
  const rawOverallSeverity = stringAt(rawSummary, "overall_severity");
  const overallSeverity = rawOverallSeverity
    ? severityFromValue(rawOverallSeverity)
    : counts.blocking > 0
      ? "blocking"
      : counts.needsReview > 0
        ? "needs_review"
        : counts.safe > 0
          ? "safe"
          : "info";
  return {
    overallSeverity,
    ...counts
  };
}

function manualStepsFrom(diff: Record<string, unknown>): string[] {
  const raw = listAt(diff, "manual_setup_steps");
  return raw
    .map((step) => {
      if (typeof step === "string") return step;
      const record = asRecord(step);
      return stringAt(record, "text");
    })
    .filter(Boolean);
}

function countsFrom(record: Record<string, unknown>): Record<string, number> {
  const names = ["labware", "liquid_class", "device_alias", "worklist", "carrier"];
  return Object.fromEntries(names.map((name) => [name, numberAt(record, `${name}_count`) ?? 0]));
}

function mergeCounts(parts: Record<string, number>[]): Record<string, number> {
  const merged: Record<string, number> = {};
  parts.forEach((part) => {
    Object.entries(part).forEach(([key, value]) => {
      merged[key] = Math.max(merged[key] || 0, value);
    });
  });
  return merged;
}

function deckLocation(record: Record<string, unknown>): string {
  return (
    stringAt(record, "deck_location") ||
    [stringAt(record, "location"), stringAt(record, "position")].filter(Boolean).join(" ") ||
    stringAt(record, "required_deck_location") ||
    stringAt(record, "source_deck_location")
  );
}

function aliasKindFromName(name: string): string {
  const lower = name.toLowerCase();
  if (lower.includes("liquid")) return "liquid_class_alias";
  if (lower.includes("device")) return "device_alias";
  if (lower.includes("catalog")) return "catalog_alias";
  return "labware_alias";
}

function parseAliasYamlLine(line: string): [string, string] {
  const separatorIndex = line.includes('":') ? line.indexOf('":') : line.includes("':") ? line.indexOf("':") : line.indexOf(":");
  if (separatorIndex < 0) return ["", ""];
  const rawKey = line.slice(0, separatorIndex + (line[separatorIndex] === ":" ? 0 : 1));
  const rawValue = line.slice(separatorIndex + (line[separatorIndex] === ":" ? 1 : 2));
  return [cleanYamlScalar(rawKey), cleanYamlScalar(rawValue)];
}

function cleanYamlScalar(value: string): string {
  return value.trim().replace(/^['"]/, "").replace(/['"]$/, "");
}

function aliasRepairsFromRepairMarkdown(text: string, source: string): WorktableRepairModel["aliasRepairs"] {
  const actionLine = text.match(/Actions:\s*`?(\d+)/i);
  const count = actionLine ? Number(actionLine[1]) : 0;
  if (!count) return [];
  return [
    {
      id: `${source}-summary`,
      kind: "repair_plan",
      status: "needs_review",
      oldName: "",
      newName: "",
      summary: `${count} repair action${count === 1 ? "" : "s"} reported in ${source}.`,
      source,
      line: null
    }
  ];
}

function dedupeById<T extends { id: string }>(items: T[]): T[] {
  return Array.from(new Map(items.map((item) => [item.id, item])).values());
}

function firstString(values: Array<string | undefined>): string {
  return values.find((value) => Boolean(value)) || "";
}

function mergeEffectCounts(parts: Record<string, number>[], commands: CommandModel[]): Record<string, number> {
  const merged: Record<string, number> = {};
  parts.forEach((part) => {
    Object.entries(part).forEach(([key, value]) => {
      merged[key] = (merged[key] || 0) + value;
    });
  });
  // Fall back to counting effects directly from the command list when no bundle
  // supplied precomputed effect counts (e.g. XSCR/GWL-only loads). Seeding keys
  // with 0 here would defeat this fallback, so only count when nothing merged.
  if (!Object.keys(merged).length) {
    commands.forEach((command) => {
      if (!command.effect) return;
      merged[command.effect] = (merged[command.effect] || 0) + 1;
    });
  }
  return merged;
}

function operationFromCommandId(commandId: string): string {
  const lower = commandId.toLowerCase();
  if (/addlabware|load_labware/.test(lower)) return "add_labware";
  if (/pickup|pick_up|gettips/.test(lower)) return "pick_up_tips";
  if (/settipsback|set_tips_back|returntips/.test(lower)) return "set_tips_back";
  if (/droptips|drop_tips/.test(lower)) return "drop_tips";
  if (/getheadadapter|get_head_adapter/.test(lower)) return "get_head_adapter";
  if (/dropheadadapter|drop_head_adapter/.test(lower)) return "drop_head_adapter";
  if (/aspirate/.test(lower)) return lower.includes("liha") ? "liha_aspirate" : "aspirate";
  if (/dispense/.test(lower)) return lower.includes("liha") ? "liha_dispense" : "dispense";
  if (/mix/.test(lower)) return lower.includes("liha") ? "liha_mix" : "mix";
  if (/wash/.test(lower)) return "wash";
  if (/prompt|userprompt/.test(lower)) return "prompt_user";
  if (/loop/.test(lower)) return "loop_over_wells";
  if (/comment/.test(lower)) return "comment";
  if (/subroutine|execute|macro|detect|variable|report/.test(lower)) return "opaque";
  if (/cga|rga/.test(lower)) return "move_plate";
  return "";
}

function familyForOperation(operation: string): OperationFamily {
  const lower = operation.toLowerCase();
  if (lower.includes("aspirate") || lower.includes("dispense") || lower.includes("mix")) return "liquid";
  if (lower.includes("tip") || lower.includes("adapter")) return "tips";
  if (lower.includes("labware")) return "labware";
  if (lower.includes("move")) return "motion";
  if (lower.includes("prompt")) return "prompt";
  if (lower.includes("loop")) return "flow";
  if (lower.includes("wash")) return "wash";
  if (lower.includes("comment")) return "comment";
  if (lower.includes("initialize")) return "setup";
  return "opaque";
}

function effectForOperation(operation: string): string {
  const family = familyForOperation(operation);
  if (family === "liquid") return "liquid_transfer";
  if (family === "tips") return "tip_state_change";
  if (family === "labware" || family === "motion") return "labware_movement";
  if (family === "comment") return "no_effect";
  if (family === "prompt" || family === "flow") return "opaque";
  if (family === "wash") return "tip_state_change";
  return "opaque";
}

function emptyHeadMetadata(): CommandHeadMetadata {
  return {
    headKind: "unknown",
    channelCount: null,
    activeTipCount: null,
    selectedTipIndexes: [],
    tipSpacingXmm: null,
    tipSpacingYmm: null
  };
}

function headMetadataFromXscrNode(node: Element, commandId: string, wells: string[]): CommandHeadMetadata {
  const selectedTipIndexes = selectedTipIndexesFromNode(node);
  const xCount = numberFromText(node, "XCount");
  const yCount = numberFromText(node, "YCount");
  const headKind = headKindFromText(`${commandId} ${textOf(node, "DeviceAlias")} ${textOf(node, "AvailableID")}`);
  const channelCount =
    headKind === "mca"
      ? xCount && yCount
        ? xCount * yCount
        : channelCountFromText(commandId, headKind)
      : channelCountFromText(commandId, headKind);
  const tipSpacing = numberFromText(node, "TipSpacing");
  const xSpacing = numberFromText(node, "XSpacing") ?? tipSpacing;
  const ySpacing = numberFromText(node, "YSpacing");
  const activeTipCount = selectedTipIndexes.length || wells.length || channelCount;

  return {
    headKind,
    channelCount,
    activeTipCount: activeTipCount || null,
    selectedTipIndexes,
    tipSpacingXmm: xSpacing,
    tipSpacingYmm: ySpacing
  };
}

function headMetadataFromXml(rawXml: string): CommandHeadMetadata {
  if (!rawXml.trim()) return emptyHeadMetadata();
  try {
    const document = new DOMParser().parseFromString(rawXml, "application/xml");
    const parserError = document.querySelector("parsererror");
    if (parserError) return emptyHeadMetadata();
    const commandNode = Array.from(document.querySelectorAll("Object")).find((node) => operationFromCommandId(node.getAttribute("Type") || ""));
    if (!commandNode) return emptyHeadMetadata();
    const commandId = (commandNode.getAttribute("Type") || "").split(".").pop() || commandNode.firstElementChild?.tagName || "";
    const selectedWells = wellsFromSelectionString(textOf(commandNode, "SelectedWellsString"));
    const firstX = numberFromText(commandNode, "FirstTipXPosition");
    const firstY = numberFromText(commandNode, "FirstTipYPosition");
    const lastX = numberFromText(commandNode, "LastTipXPosition") ?? firstX;
    const lastY = numberFromText(commandNode, "LastTipYPosition") ?? firstY;
    const rectangleWells =
      firstX !== null && firstY !== null && lastX !== null && lastY !== null ? wellsFromTipRectangle(firstX, firstY, lastX, lastY) : [];
    return headMetadataFromXscrNode(commandNode, commandId, selectedWells.length ? selectedWells : rectangleWells);
  } catch {
    return emptyHeadMetadata();
  }
}

function headMetadataFromRecord(
  step: Record<string, unknown>,
  parameters: Record<string, unknown>,
  fallback: CommandHeadMetadata,
  wells: string[]
): CommandHeadMetadata {
  const text = [
    stringAt(step, "operation"),
    stringAt(step, "command_id"),
    stringAt(step, "name"),
    stringAt(step, "step_type"),
    stringAt(parameters, "device"),
    stringAt(parameters, "device_alias"),
    stringAt(parameters, "head"),
    stringAt(parameters, "head_kind")
  ].join(" ");
  const inferredKind = headKindFromText(text);
  const headKind = fallback.headKind !== "unknown" ? fallback.headKind : inferredKind !== "unknown" ? inferredKind : headKindFromWellCount(wells);
  const selectedTipIndexes = numberListAt(parameters, "selected_tip_indexes");
  const channelCount = numberAt(parameters, "channel_count") ?? fallback.channelCount ?? channelCountFromText(text, headKind);
  const activeTipCount =
    numberAt(parameters, "active_tip_count") ??
    fallback.activeTipCount ??
    (selectedTipIndexes.length || wells.length || null);
  const tipSpacing = numberAt(parameters, "tip_spacing_mm") ?? numberAt(parameters, "tip_spacing") ?? fallback.tipSpacingXmm;

  return {
    headKind,
    channelCount,
    activeTipCount,
    selectedTipIndexes: selectedTipIndexes.length ? selectedTipIndexes : fallback.selectedTipIndexes,
    tipSpacingXmm: numberAt(parameters, "tip_spacing_x_mm") ?? tipSpacing,
    tipSpacingYmm: numberAt(parameters, "tip_spacing_y_mm") ?? fallback.tipSpacingYmm
  };
}

function selectedTipIndexesFromNode(node: ParentNode): number[] {
  const container = node.querySelector("SelectedTipsIndexes");
  if (!container) return [];
  return Array.from(container.querySelectorAll("int"))
    .map((item) => Number(item.textContent?.trim()))
    .filter((item) => Number.isFinite(item));
}

function numberListAt(value: unknown, key: string): number[] {
  const item = asRecord(value)[key];
  if (!Array.isArray(item)) return [];
  return item.map((entry) => (typeof entry === "number" ? entry : Number(entry))).filter((entry) => Number.isFinite(entry));
}

function headKindFromText(text: string): HeadKind {
  const lower = text.toLowerCase();
  if (lower.includes("liha") || lower.includes("li ha")) return "liha";
  if (lower.includes("mca")) return "mca";
  return "unknown";
}

function headKindFromWellCount(wells: string[]): HeadKind {
  if (!wells.length) return "unknown";
  return wells.length <= 8 ? "liha" : "mca";
}

function channelCountFromText(text: string, headKind: HeadKind): number | null {
  const lower = text.toLowerCase();
  if (headKind === "liha") return 8;
  if (headKind === "mca") {
    if (lower.includes("384")) return 384;
    return 96;
  }
  return null;
}

function inferCatalogName(label: string): string {
  return inferCatalogNameFromLabel(label);
}

function colorForLabware(catalogName: string, label: string, role = ""): string {
  const text = `${catalogName} ${label} ${role}`.toLowerCase();
  if (text.includes("fluent:structural")) return "#9ca3a9";
  if (text.includes("fluent:carrier") || text.includes("fluent:nest")) return "#b3b8bc";
  if (text.includes("fluent:device")) return "#a8b1c2";
  if (text.includes("tip")) return "#d8dde4";
  if (text.includes("wash")) return "#8ec9c1";
  if (text.includes("adapter")) return "#a4aab2";
  if (text.includes("filter")) return "#e7b86b";
  if (text.includes("collection") || text.includes("dest")) return "#7fb8d8";
  if (text.includes("source") || text.includes("tube")) return "#8fc483";
  return "#cfd5dc";
}

// Upper bound on wells materialised from a single rectangle / range expansion.
// Guards against malformed or hostile coordinates (e.g. FirstTipXPosition far
// from LastTipXPosition, or a "A1-Z999" selection) allocating millions of
// strings and freezing the tab. 1536 covers a 32x48 (1536-well) plate.
const MAX_EXPANDED_WELLS = 1536;

function wellsFromTipRectangle(firstX: number, firstY: number, lastX: number, lastY: number): string[] {
  if (!Number.isFinite(firstX) || !Number.isFinite(firstY) || !Number.isFinite(lastX) || !Number.isFinite(lastY)) return [];
  const wells: string[] = [];
  const xStart = Math.min(firstX, lastX);
  const xEnd = Math.max(firstX, lastX);
  const yStart = Math.min(firstY, lastY);
  const yEnd = Math.max(firstY, lastY);
  for (let y = yStart; y <= yEnd; y += 1) {
    for (let x = xStart; x <= xEnd; x += 1) {
      if (wells.length >= MAX_EXPANDED_WELLS) return wells;
      wells.push(wellIdFromRowCol(y - 1, x - 1));
    }
  }
  return wells;
}

function wellsFromSelectionString(value: string): string[] {
  const text = value.trim().toUpperCase();
  if (!text) return [];

  const wells: string[] = [];
  const rangePattern = /([A-Z]+)(\d+)\s*-\s*([A-Z]+)(\d+)/g;
  const consumedRanges: Array<[number, number]> = [];
  let rangeMatch: RegExpExecArray | null;

  while ((rangeMatch = rangePattern.exec(text))) {
    consumedRanges.push([rangeMatch.index, rangeMatch.index + rangeMatch[0].length]);
    const startRow = rowIndexFromLetters(rangeMatch[1]);
    const endRow = rowIndexFromLetters(rangeMatch[3]);
    const startCol = Number(rangeMatch[2]);
    const endCol = Number(rangeMatch[4]);
    const rowStart = Math.min(startRow, endRow);
    const rowEnd = Math.max(startRow, endRow);
    const colStart = Math.min(startCol, endCol);
    const colEnd = Math.max(startCol, endCol);
    for (let row = rowStart; row <= rowEnd; row += 1) {
      for (let col = colStart; col <= colEnd; col += 1) {
        if (wells.length >= MAX_EXPANDED_WELLS) break;
        wells.push(`${lettersFromRowIndex(row)}${col}`);
      }
      if (wells.length >= MAX_EXPANDED_WELLS) break;
    }
  }

  Array.from(text.matchAll(/\b([A-Z]+)(\d+)\b/g)).forEach((match) => {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    const insideRange = consumedRanges.some(([rangeStart, rangeEnd]) => start >= rangeStart && end <= rangeEnd);
    if (!insideRange) wells.push(`${match[1]}${Number(match[2])}`);
  });

  return Array.from(new Set(wells));
}

function wellsFromStep(parameters: Record<string, unknown>, operation: string): string[] {
  const raw = parameters.wells || parameters.well || parameters.selected_wells || parameters.selection;
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string" && raw.trim()) {
    const rangeWells = wellsFromSelectionString(raw);
    return rangeWells.length ? rangeWells : raw.split(/[,\s]+/).filter(Boolean);
  }
  if (operation.includes("loop")) return [];
  return [];
}

function wellFromGwlPosition(value: string): string {
  if (!value) return "";
  if (/^[A-P]\d{1,2}$/i.test(value)) return value.toUpperCase();
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return "";
  const index = numeric - 1;
  const row = index % DEFAULT_ROWS;
  const col = Math.floor(index / DEFAULT_ROWS);
  return wellIdFromRowCol(row, col);
}

function rowIndexFromLetters(value: string): number {
  return value.split("").reduce((total, letter) => total * 26 + letter.charCodeAt(0) - 64, 0) - 1;
}

function lettersFromRowIndex(index: number): string {
  let value = index + 1;
  let letters = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    letters = String.fromCharCode(65 + remainder) + letters;
    value = Math.floor((value - 1) / 26);
  }
  return letters || "A";
}

function wellIdFromRowCol(row: number, col: number): string {
  return `${lettersFromRowIndex(row)}${col + 1}`;
}

function titleCase(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .trim();
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "item";
}

function numericField(fields: string[]): number | null {
  for (const field of fields) {
    const value = Number(field);
    if (Number.isFinite(value) && value > 0) return value;
  }
  return null;
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

function listAt(value: Record<string, unknown> | unknown, key: string): unknown[] {
  const record = asRecord(value);
  const item = record[key];
  return Array.isArray(item) ? item : [];
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

function numberAtArray(values: unknown[], index: number): number | null {
  const value = values[index];
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function asNumberRecord(value: unknown): Record<string, number> {
  const record = asRecord(value);
  return Object.fromEntries(
    Object.entries(record)
      .map(([key, item]) => [key, typeof item === "number" ? item : Number(item)])
      .filter(([, item]) => Number.isFinite(item as number))
  ) as Record<string, number>;
}

function textOf(root: ParentNode, selector: string): string {
  // For element roots, prefer a direct child whose (namespace-stripped) local
  // name matches, so a field on this node isn't shadowed by a same-named field
  // nested inside a child <Object>. Fall back to the first descendant — and
  // always for Document roots, where the field lives deeper in the tree.
  if (root instanceof Element) {
    const direct = textOfDirect(root, selector);
    if (direct) return direct;
  }
  const element = root.querySelector(selector);
  return element?.textContent?.trim() || "";
}

function numberFromText(root: ParentNode, selector: string): number | null {
  const value = Number(textOf(root, selector));
  return Number.isFinite(value) ? value : null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
