export type ArtifactKind =
  | "protocol-ir"
  | "xscr"
  | "gwl"
  | "zeia"
  | "simulation"
  | "metadata"
  | "hardware"
  | "hardware-image"
  | "labware-catalog"
  | "connector-graph"
  | "worktable-geometry"
  | "worktable-mesh"
  | "worktable-diff"
  | "validation-diff"
  | "repair-plan"
  | "alias-map"
  | "unknown";

export type OperationFamily =
  | "setup"
  | "labware"
  | "tips"
  | "liquid"
  | "motion"
  | "prompt"
  | "flow"
  | "wash"
  | "comment"
  | "opaque";

export type WellShape = "round" | "square" | "none";

export type HeadKind = "liha" | "mca" | "unknown";

export type MotionKind = "pipetting" | "rga" | "unknown";

export type GripperFingerSet = "plate" | "tube" | "cap" | "unknown";

export type GripperOrientation = "parallel" | "diagonal" | "unknown";

export type GripperJawState = "open" | "closed" | "unknown";

export type GripperState = {
  mounted: boolean;
  fingerSet: GripperFingerSet;
  orientation: GripperOrientation;
  jaw: GripperJawState;
  grippedLabware: string[];
  grippedStack: string[];
};

export type VerificationOverlayKind = "rga_fingers" | "labware_seating" | "tube_cap_gripper";

export type VerificationOverlay = {
  kind: VerificationOverlayKind;
  expect: string;
  badStates: string[];
  title: string;
  detail: string;
};

export type VerificationStepModel = {
  group: string;
  type: "prompt" | "comment" | "subroutine" | "manual_move" | "step";
  prompt?: string;
  comment?: string;
  subroutine?: string;
  labware?: string;
  toLocation?: string;
  toSite?: number | null;
  stepId?: string;
  name?: string;
};

export type HardwareProfile =
  | "generic"
  | "plate"
  | "24-dwp"
  | "24-filter-plate"
  | "tube-holder"
  | "cap-holder"
  | "tip-box"
  | "adapter"
  | "carrier"
  | "nest"
  | "deck"
  | "device"
  | "structural"
  | "waste"
  | "wash-station"
  | "reservoir"
  | "unknown-component";

export type ComponentRole =
  | "labware"
  | "plate"
  | "tube-rack"
  | "tip-box"
  | "reservoir"
  | "wash-station"
  | "waste"
  | "cap-holder"
  | "adapter"
  | "nest"
  | "carrier"
  | "device"
  | "deck"
  | "structural"
  | "unknown";

export type ModelAssetFormat = "glb" | "gltf" | "obj" | "stl" | "unknown";

export type Vector3Model = {
  x: number;
  y: number;
  z: number;
};

export type Vector3Tuple = [number, number, number];

export type Matrix3Model = [
  [number, number, number],
  [number, number, number],
  [number, number, number]
];

export type BoundingBoxModel = {
  min: Vector3Model;
  max: Vector3Model;
  size: Vector3Model;
  center: Vector3Model;
};

export type ModelAssetTransform = {
  scale?: Vector3Tuple;
  rotationDeg?: Vector3Tuple;
  offsetMm?: Vector3Tuple;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  boundingBoxMm?: BoundingBoxModel | null;
};

export type FluentTransformModel = {
  source: string;
  positionMm: Vector3Model;
  orientation: Matrix3Model;
  dimensionMm: Vector3Model | null;
  componentGuid: string;
  connectorGuid: string;
  siteGuid: string;
  stackLevel: number;
  parentLabels: string[];
  path: string[];
  componentRole?: ComponentRole;
  meshGuid?: string;
  meshName?: string;
  meshSourcePath?: string;
  meshPayloadSize?: number;
  meshDecodedSize?: number;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  modelAssetPath?: string;
  modelAssetFormat?: ModelAssetFormat;
  modelScale?: Vector3Tuple;
  modelRotationDeg?: Vector3Tuple;
  modelOffsetMm?: Vector3Tuple;
  modelBoundingBoxMm?: BoundingBoxModel | null;
};

export type WorktableMeshModel = {
  id: string;
  guid: string;
  name: string;
  objectName: string;
  source: string;
  sourcePath: string;
  artifactId?: string;
  payloadSize: number;
  decodedSize?: number;
  base64Length?: number;
  payloadEncoding?: "base64" | "binary" | "unknown";
  archiveFormat?: string;
  convertedAssetPath?: string;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  modelAssetPath?: string;
  modelAssetFormat?: ModelAssetFormat;
  references: string[];
  notes: string[];
};

export type ModelAssetModel = {
  id: string;
  name: string;
  path: string;
  format: ModelAssetFormat;
  source: string;
  meshGuid?: string;
  meshName?: string;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  componentRole?: ComponentRole;
  hardwareProfile?: HardwareProfile;
  transform?: ModelAssetTransform;
  references: string[];
};

export type FluentAssetSourceType = "host-db" | "zeia" | "procedural" | "stub";

export type FluentAssetDimensions = {
  xMm: number | null;
  yMm: number | null;
  zMm: number | null;
};

export type FluentTextureRef = {
  textureId: string | null;
  textureGuid: string | null;
  position: string;
  assetPath: string | null;
  format?: string | null;
};

export type FluentTextureManifestEntry = {
  textureGuid: string;
  objectName: string;
  sourcePath: string;
  sourceType: FluentAssetSourceType;
  assetPath: string;
  format: string;
  byteLength: number;
  checksum?: string | null;
  priority?: boolean;
};

export type FluentConnectorSnapPoint = {
  connectorGuid?: string | null;
  positionMm?: [number, number, number] | null;
  orientation?: number[][] | null;
  childComponentGuid?: string | null;
};

export type FluentSnapAnchor = {
  siteGuid: string | null;
  locationGroupName?: string | null;
  typeName?: string | null;
  dimensionsMm?: [number, number, number] | null;
  compatibleChildGuids: string[];
  connectorCount?: number;
  snapPoint?: FluentConnectorSnapPoint | null;
};

export type FluentChildConnector = {
  guid: string;
  childComponentGuid: string;
  siteGuid: string;
  sourcePath?: string;
  isDefault?: boolean;
  positionMm?: [number, number, number] | null;
  orientation?: number[][] | null;
  name?: string | null;
  description?: string | null;
};

export type FluentCompatibilityExample = {
  connectorGuid?: string | null;
  childComponentGuid?: string | null;
  childComponentName?: string | null;
  parentComponentGuid?: string | null;
  parentComponentName?: string | null;
  siteGuid?: string | null;
  positionMm?: [number, number, number] | null;
  source?: string;
};

export type FluentCompatibilityCheck = {
  id: string;
  description: string;
  verified: boolean;
  exampleCount: number;
  examples: FluentCompatibilityExample[];
};

export type FluentConnectorGraphModel = {
  schemaVersion: number;
  kind: "fluent-connector-graph";
  generatedAt: string;
  summary: {
    connectorCount: number;
    siteCount: number;
    componentCount: number;
    compatibilityCheckCount: number;
    verifiedChecks?: number;
  };
  verification?: Array<{
    componentGuid: string;
    componentName: string;
    expectedCount: number;
    actualCount: number;
    matches: boolean;
  }>;
  compatibilityChecks: FluentCompatibilityCheck[];
};

export type FluentAssetRegistryEntry = {
  meshGuid: string | null;
  componentGuid: string | null;
  objectName: string | null;
  componentName: string | null;
  renderer: string | null;
  sourcePath: string | null;
  sourceType: FluentAssetSourceType | null;
  dimensions: FluentAssetDimensions | null;
  bounds: BoundingBoxModel | null;
  boundsMm: BoundingBoxModel | null;
  vertexCount: number | null;
  triangleCount: number | null;
  assetPath: string | null;
  siteIds: string[];
  connectorIds: string[];
  textureIds: string[];
  textures: FluentTextureRef[];
  aliases: string[];
  snapAnchors?: FluentSnapAnchor[];
  childConnectors?: FluentChildConnector[];
};

export type FluentAssetRegistryModel = {
  schemaVersion: number;
  kind: "fluent-asset-registry";
  generatedAt: string;
  sources: {
    installPath: string;
    installSourceType: FluentAssetSourceType;
    manifestPath: string | null;
    aliasesPath: string | null;
    proceduralCatalogPath: string | null;
    hardwareManifestPath: string | null;
    textureManifestPath?: string | null;
    meshManifestCount: number;
    componentCount: number;
  };
  summary: {
    entryCount: number;
    meshEntries: number;
    componentOnlyEntries: number;
    proceduralEntries: number;
    aliasCount: number;
    textureCount?: number;
    entriesWithTextures?: number;
  };
  textures?: FluentTextureManifestEntry[];
  compatibilityChecks?: FluentCompatibilityCheck[];
  connectorVerification?: FluentConnectorGraphModel["verification"];
  entries: FluentAssetRegistryEntry[];
};

export type WellLayer = {
  reagent: string;
  volumeUl: number;
};

export type WellModel = {
  id: string;
  row: number;
  col: number;
  positionMm?: Vector3Model;
  positionSource?: "fluent-pipettable" | "fluent-arrangement" | "catalog";
  volumeUl: number;
  capacityUl: number;
  maxVolumeUl: number;
  deadVolumeUl: number;
  diameterMm: number;
  depthMm: number;
  reagent: string;
  layers: WellLayer[];
};

export type SnapSiteModel = {
  index?: number;
  row?: number;
  col?: number;
  positionMm: Vector3Model;
  source?: "fluent-pipettable" | "fluent-arrangement" | "catalog";
  siteGuid?: string;
  siteDimensionMm?: Vector3Model | null;
  siteLocationGroupName?: string;
  compatibleComponentGuids?: string[];
  wellShape?: WellShape;
  diameterMm?: number;
  depthMm?: number;
};

export type LabwareModel = {
  id: string;
  label: string;
  catalogName: string;
  geometryName: string;
  geometrySource: "catalog" | "inferred";
  role: string;
  componentRole?: ComponentRole;
  source: string;
  location: string;
  position: number | null;
  positionExpression?: string;
  slotIndex: number;
  rows: number;
  cols: number;
  wellShape: WellShape;
  capacityUl: number;
  maxVolumeUl: number;
  deadVolumeUl: number;
  physicalWidthMm: number;
  physicalDepthMm: number;
  physicalHeightMm: number;
  pitchXMm: number;
  pitchYMm: number;
  wellDiameterMm: number;
  wellDepthMm: number;
  color: string;
  transform: FluentTransformModel | null;
  wells: WellModel[];
  snapSites?: SnapSiteModel[];
  hardwareProfile: HardwareProfile;
  /** Exact ZEIA `.xcmp` FunctionalGroup when known. */
  functionalGroup?: string;
  hardwareAssetIds: string[];
  hardwareAssetNames: string[];

  /**
   * Optional ZEIA / Fluent mesh metadata.
   *
   * These fields let the simulator preserve the relationship between a parsed
   * Fluent component and the native .xmsh mesh stored in a ZEIA archive. The
   * React scene should render modelAssetPath when a converted .glb/.gltf exists,
   * then fall back to primitive geometry when no converted asset is available.
   */
  meshGuid?: string;
  meshName?: string;
  meshSourcePath?: string;
  meshPayloadSize?: number;
  meshDecodedSize?: number;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;

  /**
   * Optional web-ready model asset metadata.
   *
   * The preferred runtime asset format is .glb/.gltf. Native .xmsh parsing should
   * remain an offline conversion path.
   */
  modelAssetPath?: string;
  modelAssetFormat?: ModelAssetFormat;
  modelScale?: Vector3Tuple;
  modelRotationDeg?: Vector3Tuple;
  modelOffsetMm?: Vector3Tuple;
  modelBoundingBoxMm?: BoundingBoxModel | null;

  /**
   * Explicit render hints for the 3D scene.
   *
   * liquidCapable=false should be used for carriers, nests, adapters, deck
   * structures, devices, and other components that should not receive liquid
   * overlays even when their names are unknown.
   */
  renderAsModel?: boolean;
  liquidCapable?: boolean;
};

export type LabwareDefinitionModel = {
  catalogName: string;
  componentName: string;
  role: string;
  /** Exact ZEIA `.xcmp` FunctionalGroup. */
  functionalGroup?: string;
  dimensionMm: Vector3Model | null;
  /** WorktableMesh GUID from .xcmp — install/ZEIA-correct GLB key. */
  meshGuid?: string;
  meshGuids?: string[];
  meshNames?: string[];
  rows?: number;
  cols?: number;
  pitchXMm?: number;
  pitchYMm?: number;
  wellShape?: WellShape;
  wellDiameterMm?: number;
  wellDepthMm?: number;
  wellSites: Array<{
    index?: number;
    row?: number;
    col?: number;
    positionMm: Vector3Model;
    source?: WellModel["positionSource"];
    wellShape?: WellShape;
    diameterMm?: number;
    depthMm?: number;
  }>;
};

export type LabwareTransferModel = {
  labware: string;
  location: string;
  site: string;
  fixedSite: boolean;
  moveToBase: boolean;
  anchorLabware?: string;
  destinationKind: "base" | "cover_site" | "fixed_site" | "unknown";
  rawLocation?: string;
  rawSite?: string;
};

export type CommandModel = {
  id: string;
  index: number;
  source: string;
  operation: string;
  family: OperationFamily;
  name: string;
  group: string;
  commandId: string;
  effect: string;
  targetLabware: string;
  wells: string[];
  volumeUl: number | null;
  liquidClass: string;
  headKind: HeadKind;
  channelCount: number | null;
  activeTipCount: number | null;
  selectedTipIndexes: number[];
  tipSpacingXmm: number | null;
  tipSpacingYmm: number | null;
  message: string;
  rawXml: boolean;
  motionKind?: MotionKind;
  verificationOverlay?: VerificationOverlay;
  labwareTransfer?: LabwareTransferModel;
};

export type TipState = {
  picked: boolean;
  adapter: boolean;
  volumeUl: number;
  activeLabware: string;
  headKind: HeadKind;
  channelCount: number | null;
  activeTipCount: number;
  tipSpacingXmm: number | null;
  tipSpacingYmm: number | null;
};

export type SnapshotModel = {
  index: number;
  activeCommandId: string;
  tipState: TipState;
  gripperState: GripperState;
  labwareVolumes: Record<string, Record<string, number>>;
  labwareLocations: Record<string, LabwareTransferModel>;
};

export type HardwareContext = {
  assets: number;
  packagedAssets: number;
  connectors: number;
  pins: number;
  missingAssets: string[];
  notes: string[];
  assetDetails: HardwareAssetModel[];
  worktableMeshes?: WorktableMeshModel[];
  modelAssets?: ModelAssetModel[];
};

export type HardwareAssetModel = {
  id: string;
  name: string;
  status: string;
  packaged: boolean;
  manualVerificationRequired: boolean;
  bundlePath: string;
  sourcePath: string;
  entry: string;
  imageSrc: string;
  references: string[];
  profile: HardwareProfile;
  matchedLabware: string[];
  componentRole?: ComponentRole;
  meshGuid?: string;
  meshName?: string;
  meshSourcePath?: string;
  meshPayloadSize?: number;
  meshDecodedSize?: number;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  modelAssetPath?: string;
  modelAssetFormat?: ModelAssetFormat;
  modelScale?: Vector3Tuple;
  modelRotationDeg?: Vector3Tuple;
  modelOffsetMm?: Vector3Tuple;
  modelBoundingBoxMm?: BoundingBoxModel | null;
};

export type SourceArtifact = {
  id: string;
  name: string;
  kind: ArtifactKind;
  source: string;
  text: string;
  size: number;
  dataUrl?: string;
  mimeType?: string;
  path?: string;

  /**
   * Optional extracted metadata for .xmsh artifacts.
   *
   * loaders.ts may populate these when reading ZEIA-contained mesh files, while
   * parsers.ts / fluentGeometry.ts can also derive them from the XML body.
   */
  meshGuid?: string;
  meshName?: string;
  meshSourcePath?: string;
  meshPayloadSize?: number;
  meshDecodedSize?: number;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  modelAssetPath?: string;
  modelAssetFormat?: ModelAssetFormat;
};

export type DiffSeverity = "blocking" | "needs_review" | "safe" | "info";

export type WorktableIssueKind =
  | "missing_labware"
  | "changed_position"
  | "liquid_class"
  | "device_alias"
  | "worklist"
  | "carrier"
  | "tip_box"
  | "worktable"
  | "warning"
  | "validation";

export type WorktableIssue = {
  id: string;
  kind: WorktableIssueKind;
  severity: DiffSeverity;
  status: string;
  label: string;
  detail: string;
  sourceValue: string;
  targetValue: string;
  suggestion: string;
  diffPath: string;
};

export type ValidationHighlight = {
  labwareLabel: string;
  slotIndex?: number;
  severity: DiffSeverity;
  reasons: string[];
};

export type ValidationGate = {
  id: string;
  gate: string;
  gateNumber: number;
  name: string;
  status: string;
  severity: DiffSeverity;
  summary: string;
  details: Record<string, unknown>;
  source: string;
};

export type AliasRepair = {
  id: string;
  kind: string;
  status: string;
  oldName: string;
  newName: string;
  summary: string;
  source: string;
  line: number | null;
};

export type WorktableRepairModel = {
  summary: {
    overallSeverity: DiffSeverity;
    blocking: number;
    needsReview: number;
    safe: number;
    info: number;
  };
  sourceWorktable: string;
  protocolWorktable: string;
  sourceCounts: Record<string, number>;
  protocolCounts: Record<string, number>;
  issues: WorktableIssue[];
  aliasRepairs: AliasRepair[];
  manualSteps: string[];
  validationChecks: WorktableIssue[];
  validationGates: ValidationGate[];
};

export type SourceSummary = {
  name: string;
  kind: ArtifactKind;
  count: number;
};

export type ReadinessState = {
  status: string;
  summary?: string;
  [key: string]: unknown;
};

export type ProtocolReadiness = {
  offline_validation: ReadinessState;
  review_state: ReadinessState;
  fluentcontrol_load_diagnostic: ReadinessState;
  generated_zeia_import: ReadinessState;
  script_editor_load: ReadinessState;
  simulation: ReadinessState;
  hardware_run: ReadinessState;
};

export type ProtocolModel = {
  id: string;
  name: string;
  context: string;
  worktableName: string;
  readinessStatus?: string;
  readiness?: ProtocolReadiness | null;
  sourceSummaries: SourceSummary[];
  labware: LabwareModel[];
  commands: CommandModel[];
  snapshots: SnapshotModel[];
  hardware: HardwareContext;
  repairs: WorktableRepairModel;
  warnings: string[];
  effectCounts: Record<string, number>;
  worktableMeshes?: WorktableMeshModel[];
  modelAssets?: ModelAssetModel[];
};

export type SceneFocusTargetKind =
  | "command"
  | "labware"
  | "site"
  | "liquid"
  | "mesh"
  | "component"
  | "model"
  | "hardware-asset"
  | "editor-object";

export type SceneFocusTarget = {
  kind: SceneFocusTargetKind;
  label: string;
  labwareLabel?: string;
  slotIndex?: number;
  commandId?: string;
  meshGuid?: string;
  modelAssetPath?: string;
};


export type SceneSnapPlacementTarget = {
  key: string;
  label: string;
  source: "fluent-site" | "deck-slot";
  parentLabel?: string;
  parentKind?: ComponentRole | HardwareProfile | "structural" | "unknown";
  siteGuid?: string;
  siteLocationGroupName?: string;
  compatibleComponentGuids?: string[];
};

export type ScenePlacementOverride = {
  id: string;
  label: string;
  labwareLabel: string;
  position: Vector3Model;
  rotationY: number;
  source: "user-drag" | "imported" | "script";
  snapTarget?: SceneSnapPlacementTarget;
  updatedAt: string;
};

export type HardwareAssetImageOverride = {
  assetId: string;
  assetName: string;
  imageSrc: string;
  mimeType?: string;
  fileName?: string;
  sourcePath?: string;
  updatedAt: string;
};

export type ScriptEditorFieldId =
  | "name"
  | "operation"
  | "targetLabware"
  | "wells"
  | "volumeUl"
  | "liquidClass"
  | "message"
  | "general";

export type ScriptValidationIssue = {
  field: ScriptEditorFieldId;
  message: string;
};

export type ScriptEditorCommand = {
  id: string;
  sourceCommandId?: string;
  name: string;
  operation: string;
  family: OperationFamily;
  targetLabware: string;
  wells: string[];
  volumeUl: number | null;
  liquidClass: string;
  message?: string;
  specs?: Record<string, string>;
  enabled: boolean;
  dirty: boolean;
  validationIssues: ScriptValidationIssue[];
  validationMessages: string[];
};

export type ObjectLibraryItem = {
  id: string;
  name: string;
  profile: HardwareProfile;
  componentRole?: ComponentRole;
  modelAssetPath?: string;
  imageSrc?: string;
  rows?: number;
  cols?: number;
  wellShape?: WellShape;
  capacityUl?: number;
  maxVolumeUl?: number;
  deadVolumeUl?: number;
  physicalWidthMm?: number;
  physicalDepthMm?: number;
  physicalHeightMm?: number;
  pitchXMm?: number;
  pitchYMm?: number;
  wellDiameterMm?: number;
  wellDepthMm?: number;
  liquidCapable?: boolean;
  color?: string;
  tags: string[];
};

export type SceneObjectDragPayload = {
  item: ObjectLibraryItem;
  startedAt: number;
};

export type SceneObjectDropPlacement = {
  position: Vector3Model;
  slotIndex: number;
  rotationY: number;
  snapTarget?: SceneSnapPlacementTarget;
};

export type SceneInteractionMode = "view" | "edit";

export const SCENE_DIAGNOSTICS_KIND = "scene-diagnostics";
export const SCENE_DIAGNOSTICS_SCHEMA_VERSION = 1;

export type SceneObjectPositionDiagnostic = {
  source: "fluent_transform" | "slot_grid" | "placement_override" | "unknown";
  fluentMm?: Vector3Model;
  scene: Vector3Model;
  rotationY: number;
};

export type SceneObjectRenderDiagnostic = {
  mode: "glb" | "primitive_fallback" | "editor_object";
  modelAssetPath?: string;
  modelAssetStatus: "found" | "missing" | "not_checked" | "none";
  meshGuid?: string;
  meshName?: string;
};

export type SceneObjectDiagnostic = {
  id: string;
  label: string;
  catalogName: string;
  role: string;
  componentRole?: string;
  location: string;
  positionIndex: number | null;
  position: SceneObjectPositionDiagnostic;
  render: SceneObjectRenderDiagnostic;
  liquidCapable?: boolean;
  anchored: boolean;
};

export type PromptTargetDiagnostic = {
  commandId: string;
  commandIndex: number;
  stepId?: string;
  group: string;
  prompt: string;
  overlayKind?: string;
  expect?: string;
  badStates?: string[];
  focusLabware?: string;
};

export type ActiveCommandDiagnostic = {
  id: string;
  index: number;
  operation: string;
  family: string;
  name: string;
  group: string;
  targetLabware: string;
  message: string;
  motionKind?: string;
  verificationOverlay?: VerificationOverlay;
  gripperState?: GripperState;
};

export type SceneDiagnosticsModel = {
  exportedAt: string;
  app: "tecan-protocol-simulator";
  kind: typeof SCENE_DIAGNOSTICS_KIND;
  schemaVersion: typeof SCENE_DIAGNOSTICS_SCHEMA_VERSION;
  protocol: {
    id: string;
    name: string;
    worktableName: string;
    context: string;
  };
  playback: {
    activeIndex: number;
    commandCount: number;
    staticMode?: boolean;
  };
  activeCommand: ActiveCommandDiagnostic | null;
  promptTargets: PromptTargetDiagnostic[];
  objects: SceneObjectDiagnostic[];
  unresolvedModels: Array<{
    label?: string;
    meshGuid?: string;
    meshName?: string;
    modelAssetPath: string;
    reason: string;
  }>;
  fallbackMeshes: Array<{
    label?: string;
    meshGuid?: string;
    meshName?: string;
    modelAssetPath?: string;
    reason: string;
  }>;
  warnings: string[];
  validationGates: Array<{
    id: string;
    gate: string;
    gateNumber: number;
    name: string;
    status: string;
    severity: DiffSeverity;
    summary: string;
    source: string;
  }>;
  geometryCoverage?: {
    transformedWellLabware: number;
    exactTransformedWellLabware: number;
    commandedWellTargets: number;
    exactCommandedWellTargets: number;
    anchoredProtocolLocations: number;
    fallbackCommandedTargets: string[];
  };
  gripper?: GripperState;
  sceneFocus?: SceneFocusTarget | null;
};

export type SimulatorEditorState = {
  version: 1;
  scriptCommands: ScriptEditorCommand[];
  objectLibrary: ObjectLibraryItem[];
  placementOverrides: ScenePlacementOverride[];
  imageOverrides: HardwareAssetImageOverride[];
  notes: string[];
};

export type SimulatorExportBundle = {
  exportedAt: string;
  protocol: ProtocolModel;
  editor: SimulatorEditorState;
};

export type SampleFile = {
  id: string;
  kind: ArtifactKind;
  label: string;
  path: string;
};

export type ProtocolBuildOptions = {
  primaryScriptArtifactKey?: string;
};

export type SampleDataset = {
  id: string;
  name: string;
  description: string;
  files: SampleFile[];
  bundleRevision?: string;
};

export type ParsedBundle = {
  labware: LabwareModel[];
  commands: CommandModel[];
  labwareDefinitions?: LabwareDefinitionModel[];
  metadata: Partial<ProtocolModel>;
  hardware: Partial<HardwareContext>;
  repairs: Partial<WorktableRepairModel>;
  warnings: string[];
  effectCounts: Record<string, number>;
  worktableMeshes?: WorktableMeshModel[];
  modelAssets?: ModelAssetModel[];
};
