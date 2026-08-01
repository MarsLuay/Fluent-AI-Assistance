import { Suspense, lazy, useCallback, useEffect, useMemo, useReducer, useRef, useState, type FocusEvent as ReactFocusEvent } from "react";
import type { CSSProperties, ChangeEvent, DragEvent, KeyboardEvent as ReactKeyboardEvent, MutableRefObject, PointerEvent as ReactPointerEvent } from "react";
import {
  AlertTriangle,
  Box,
  CheckCircle2,
  Copy,
  Download,
  Eye,
  FileArchive,
  FlaskConical,
  GitCompareArrows,
  GripVertical,
  Image as ImageIcon,
  Layers3,
  ListChecks,
  LocateFixed,
  Pause,
  Pencil,
  Play,
  Plus,
  Redo2,
  RotateCcw,
  Save,
  TestTube2,
  Trash2,
  Undo2,
  Upload,
  Wrench
} from "lucide-react";
import JSZip from "jszip";
import { fetchSamples, loadFiles, loadSample } from "./data/loaders";
import { buildProtocolModel } from "./data/parsers";
import { rebuildProtocolWithScriptCommands, scriptCommandsFromProtocolCommands } from "./sim/state";
import { INITIAL_SCRIPT_TITLE_STATE, scriptTitleReducer } from "./state/scriptTitleState";
import type {
  CommandModel,
  DiffSeverity,
  HardwareAssetModel,
  HardwareAssetImageOverride,
  LabwareTransferModel,
  LabwareModel,
  ObjectLibraryItem,
  OperationFamily,
  ProtocolModel,
  ReadinessState,
  SampleDataset,
  SceneInteractionMode,
  SceneObjectDragPayload,
  SceneObjectDropPlacement,
  SceneFocusTarget,
  ScenePlacementOverride,
  ScriptEditorCommand,
  ScriptValidationIssue,
  SourceArtifact,
  ValidationGate,
  ValidationHighlight,
  WellModel,
  WellShape,
  WorktableIssue,
  WorktableRepairModel
} from "./types";
import "./styles.css";

const DeckScene = lazy(() => import("./components/DeckScene").then((module) => ({ default: module.DeckScene })));

function DeckSceneFallback() {
  return (
    <div className="deck-scene deck-scene-fallback" role="status" aria-live="polite">
      <span>Loading 3D deck scene...</span>
    </div>
  );
}

const EMPTY_MODEL: ProtocolModel = {
  id: "empty",
  name: "No Protocol Loaded",
  context: "local workspace",
  worktableName: "Worktable",
  readinessStatus: "scaffold_not_validated",
  readiness: null,
  sourceSummaries: [],
  labware: [],
  commands: [],
  snapshots: [],
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
  },
  warnings: [],
  effectCounts: {}
};

const SPEEDS = [
  { label: "0.5x", value: 0.5 },
  { label: "1x", value: 1 },
  { label: "2x", value: 2 },
  { label: "4x", value: 4 }
];

const PLAYBACK_INTERVAL_MS = 900;
const MIN_PLAYBACK_INTERVAL_MS = 160;
const MAX_ARTIFACT_ROWS = 80;
const TIMELINE_COMMAND_ROW_HEIGHT = 62;
const TIMELINE_COMMAND_OVERSCAN_ROWS = 6;
const MAX_HARDWARE_ASSETS = 12;
const MAX_HARDWARE_NOTES = 3;
const MAX_ALIAS_REPAIRS = 12;
const MAX_MISSING_MESH_ROWS = 8;
const MAX_DEV_LOGS = 250;
const ACCEPTED_ARTIFACT_EXTENSIONS = ".json,.xscr,.gwl,.zeia,.zip,.xwsp,.xcmp,.xcon,.xsit,.yaml,.yml,.md,.jpg,.jpeg,.png,.gif,.webp";
const DEFAULT_LEFT_PANEL_WIDTH = 310;
const DEFAULT_RIGHT_PANEL_WIDTH = 430;
const MIN_SIDE_PANEL_WIDTH = 240;
const MAX_SIDE_PANEL_WIDTH = 560;
const WORKSPACE_PADDING_PX = 12;

type ProtocolStats = {
  labware: number;
  commands: number;
  liquidCommands: number;
  tipCommands: number;
};

type MeshInventoryItem = {
  guid: string;
  name: string;
  sourcePath: string;
  assetPath: string;
};

type MeshAssetLookup = {
  status: "idle" | "checking" | "complete";
  foundPaths: string[];
  error: string;
};

type ModelAssetCheckStatus = "found" | "missing" | "unknown";

type MeshInventorySummary = {
  items: MeshInventoryItem[];
  convertedCount: number;
  fallbackCount: number;
  missingItems: MeshInventoryItem[];
  status: MeshAssetLookup["status"];
  error: string;
};

type GeometryCoverageSummary = {
  transformedWellLabware: number;
  exactTransformedWellLabware: number;
  commandedWellTargets: number;
  exactCommandedWellTargets: number;
  anchoredProtocolLocations: number;
  fallbackCommandedTargets: string[];
};

type EditorTab = "script" | "objects";
type SidePanel = "left" | "right";
type WorkspacePanelStyle = CSSProperties & {
  "--left-panel-width": string;
  "--right-panel-width": string;
};

type DevLogLevel = "log" | "info" | "warn" | "error";

type DevLogEntry = {
  id: string;
  level: DevLogLevel;
  timestamp: string;
  message: string;
};

type CommandDetailRow = {
  label: string;
  value: string;
};

type SavedScriptArtifact = {
  fileName: string;
  body: string;
  mimeType: string;
};

type ScriptCommandPatch = Partial<Omit<ScriptEditorCommand, "id" | "sourceCommandId" | "validationIssues" | "validationMessages">>;

type CommandToolboxTemplate = {
  id: string;
  family: OperationFamily;
  name: string;
  operation: string;
  description: string;
  defaults?: Partial<Pick<ScriptEditorCommand, "targetLabware" | "wells" | "volumeUl" | "liquidClass" | "message" | "specs">>;
};

type CommandToolboxCategory = {
  family: OperationFamily;
  label: string;
  description: string;
  commands: CommandToolboxTemplate[];
};

type ObjectLibraryGeometry = {
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
  liquidCapable?: boolean;
};

type RestoredSimulatorProjectState = {
  scriptTitle?: string;
  scriptCommands: ScriptEditorCommand[];
  addedObjects: LabwareModel[];
  placementOverrides: ScenePlacementOverride[];
  imageOverrides: Record<string, string>;
  activeIndex: number | null;
  speed: number | null;
  staticMode: boolean | null;
  sceneInteractionMode: SceneInteractionMode | null;
  editorTab: EditorTab | null;
  selectedScriptCommandId: string;
};

const OPERATION_FAMILIES: OperationFamily[] = ["setup", "labware", "tips", "liquid", "motion", "prompt", "flow", "wash", "comment", "opaque"];

const COMMAND_TOOLBOX: CommandToolboxCategory[] = [
  {
    family: "setup",
    label: "Setup",
    description: "Protocol setup, variables, instrument prep, and initialization.",
    commands: [
      { id: "setup-initialize", family: "setup", name: "Initialize Script", operation: "initialize_script", description: "Start-of-script setup and instrument preparation." },
      { id: "setup-set-variable", family: "setup", name: "Set Variable", operation: "set_variable", description: "Create or update a script variable.", defaults: { specs: { variable: "", value: "" } } },
      { id: "setup-set-liquid-class", family: "setup", name: "Set Liquid Class", operation: "set_liquid_class", description: "Define the liquid class used by later liquid moves.", defaults: { liquidClass: "Water free dispense" } }
    ]
  },
  {
    family: "tips",
    label: "Tips",
    description: "Pick up, drop, and manage disposable tips and adapters.",
    commands: [
      { id: "tips-get-tip", family: "tips", name: "Get Tip", operation: "get_tip", description: "Pick up tips from the selected tip box.", defaults: { specs: { channels: "all" } } },
      { id: "tips-drop-tip", family: "tips", name: "Drop Tip", operation: "drop_tip", description: "Drop active tips to waste or a configured target.", defaults: { specs: { destination: "waste" } } },
      { id: "tips-wash-tip", family: "wash", name: "Wash Tips", operation: "wash_tips", description: "Wash active washable tips at a wash station." },
      { id: "tips-get-adapter", family: "tips", name: "Get Head Adapter", operation: "get_head_adapter", description: "Mount a head adapter before MCA or special labware steps." },
      { id: "tips-drop-adapter", family: "tips", name: "Drop Head Adapter", operation: "drop_head_adapter", description: "Return or drop a mounted head adapter." }
    ]
  },
  {
    family: "liquid",
    label: "Liquid",
    description: "Aspirate, dispense, mix, detect, and transfer liquid.",
    commands: [
      { id: "liquid-aspirate", family: "liquid", name: "Aspirate", operation: "aspirate", description: "Aspirate liquid from selected wells.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "liquid-dispense", family: "liquid", name: "Dispense", operation: "dispense", description: "Dispense liquid into selected wells.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "liquid-mix", family: "liquid", name: "Mix", operation: "mix", description: "Mix selected wells with repeated aspirate/dispense cycles.", defaults: { wells: ["A1"], volumeUl: 20, liquidClass: "Water free dispense", specs: { cycles: "3" } } },
      { id: "liquid-detect", family: "liquid", name: "Detect Liquid", operation: "detect_liquid", description: "Run liquid level detection for selected wells.", defaults: { wells: ["A1"], liquidClass: "Water free dispense" } },
      { id: "liquid-transfer", family: "liquid", name: "Transfer", operation: "transfer_liquid", description: "Move liquid from a source to a destination.", defaults: { volumeUl: 10, liquidClass: "Water free dispense", specs: { source: "", destination: "" } } }
    ]
  },
  {
    family: "labware",
    label: "Labware",
    description: "Move plates, nests, carriers, covers, and deck objects.",
    commands: [
      { id: "labware-move", family: "labware", name: "Move Labware", operation: "move_labware", description: "Move selected labware to a site or parent object.", defaults: { specs: { location: "", site: "" } } },
      { id: "labware-cover", family: "labware", name: "Cover Labware", operation: "cover_labware", description: "Place a cover or lid onto labware." },
      { id: "labware-uncover", family: "labware", name: "Uncover Labware", operation: "uncover_labware", description: "Remove a cover or lid from labware." },
      { id: "labware-register", family: "labware", name: "Register Labware", operation: "register_labware", description: "Declare or add labware for a newly-created script.", defaults: { specs: { labwareType: "", location: "" } } }
    ]
  },
  {
    family: "motion",
    label: "Motion",
    description: "Move robot arms, heads, and devices without changing liquid state.",
    commands: [
      { id: "motion-move-head", family: "motion", name: "Move Head", operation: "move_head", description: "Move the active head to a labware/site position." },
      { id: "motion-home", family: "motion", name: "Home Axis", operation: "home_axis", description: "Home an axis or device before continuing.", defaults: { specs: { axis: "" } } },
      { id: "motion-wait", family: "motion", name: "Wait", operation: "wait", description: "Wait for a duration or device state.", defaults: { specs: { seconds: "1" } } }
    ]
  },
  {
    family: "prompt",
    label: "Prompts",
    description: "Pause the run, ask the user, or require manual confirmation.",
    commands: [
      { id: "prompt-user", family: "prompt", name: "User Prompt", operation: "user_prompt", description: "Show a message and wait for user acknowledgement.", defaults: { message: "Confirm before continuing." } },
      { id: "prompt-pause", family: "prompt", name: "Pause", operation: "pause", description: "Pause execution until the operator continues.", defaults: { message: "Paused." } },
      { id: "prompt-manual-step", family: "prompt", name: "Manual Step", operation: "manual_step", description: "Document a required manual operator action.", defaults: { message: "Perform manual step, then continue." } }
    ]
  },
  {
    family: "flow",
    label: "Flow",
    description: "Control script order with loops, conditions, labels, and subroutines.",
    commands: [
      { id: "flow-if", family: "flow", name: "If Condition", operation: "if_condition", description: "Branch based on a condition.", defaults: { specs: { condition: "" } } },
      { id: "flow-loop", family: "flow", name: "Loop", operation: "loop", description: "Repeat commands for a configured count.", defaults: { specs: { count: "1" } } },
      { id: "flow-call-subroutine", family: "flow", name: "Call Subroutine", operation: "call_subroutine", description: "Run a named subroutine.", defaults: { specs: { subroutine: "" } } },
      { id: "flow-label", family: "flow", name: "Label", operation: "label", description: "Mark a location in the script.", defaults: { specs: { label: "" } } }
    ]
  },
  {
    family: "wash",
    label: "Wash",
    description: "Wash station and cleaning commands.",
    commands: [
      { id: "wash-station", family: "wash", name: "Wash Station", operation: "wash_station", description: "Run a wash station clean step." },
      { id: "wash-prime", family: "wash", name: "Prime", operation: "prime", description: "Prime lines or wash system before liquid handling." }
    ]
  },
  {
    family: "comment",
    label: "Notes",
    description: "Non-executing comments and documentation.",
    commands: [
      { id: "comment", family: "comment", name: "Comment", operation: "comment", description: "Add a non-executing script comment.", defaults: { message: "Add note here." } },
      { id: "comment-section", family: "comment", name: "Section Header", operation: "section_comment", description: "Add a visible section break in the command queue.", defaults: { message: "New section" } }
    ]
  }
];

const OBJECT_LIBRARY: ObjectLibraryItem[] = [
  {
    id: "96-well-plate",
    name: "96 Well Plate",
    profile: "plate",
    componentRole: "plate",
    rows: 8,
    cols: 12,
    wellShape: "round",
    capacityUl: 350,
    maxVolumeUl: 350,
    deadVolumeUl: 8,
    physicalWidthMm: 127.76,
    physicalDepthMm: 85.48,
    physicalHeightMm: 14.35,
    pitchXMm: 9,
    pitchYMm: 9,
    wellDiameterMm: 6.5,
    wellDepthMm: 10.8,
    liquidCapable: true,
    color: "#dbe7e5",
    tags: ["plate", "96", "standard", "liquid"]
  },
  {
    id: "24-deep-well",
    name: "24 Deep Well Plate",
    profile: "24-dwp",
    componentRole: "plate",
    rows: 4,
    cols: 6,
    wellShape: "square",
    capacityUl: 10000,
    maxVolumeUl: 10000,
    deadVolumeUl: 80,
    physicalWidthMm: 127.76,
    physicalDepthMm: 85.48,
    physicalHeightMm: 44,
    pitchXMm: 19.3,
    pitchYMm: 19.3,
    wellDiameterMm: 15.2,
    wellDepthMm: 36,
    liquidCapable: true,
    color: "#d9e0ec",
    tags: ["plate", "24", "deep well", "extraction"]
  },
  {
    id: "50ml-tube-rack",
    name: "50 mL Tube Rack",
    profile: "tube-holder",
    componentRole: "tube-rack",
    rows: 2,
    cols: 6,
    wellShape: "round",
    capacityUl: 50000,
    maxVolumeUl: 50000,
    deadVolumeUl: 500,
    physicalWidthMm: 128,
    physicalDepthMm: 45,
    physicalHeightMm: 72,
    pitchXMm: 19.5,
    pitchYMm: 19.5,
    wellDiameterMm: 28,
    wellDepthMm: 82,
    liquidCapable: true,
    color: "#e8ded4",
    tags: ["tube", "50ml", "rack", "falcon"]
  },
  {
    id: "diti-tip-box",
    name: "DiTi Tip Box",
    profile: "tip-box",
    componentRole: "tip-box",
    rows: 8,
    cols: 12,
    wellShape: "round",
    capacityUl: 0,
    maxVolumeUl: 0,
    deadVolumeUl: 0,
    physicalWidthMm: 127.76,
    physicalDepthMm: 85.48,
    physicalHeightMm: 48,
    pitchXMm: 9,
    pitchYMm: 9,
    wellDiameterMm: 3.8,
    wellDepthMm: 32,
    liquidCapable: false,
    color: "#e4ebe7",
    tags: ["tips", "diti", "box", "liha"]
  },
  {
    id: "reservoir",
    name: "Reagent Reservoir",
    profile: "reservoir",
    componentRole: "reservoir",
    rows: 1,
    cols: 8,
    wellShape: "square",
    capacityUl: 25000,
    maxVolumeUl: 25000,
    deadVolumeUl: 300,
    physicalWidthMm: 127,
    physicalDepthMm: 43,
    physicalHeightMm: 32,
    pitchXMm: 14.2,
    pitchYMm: 14.2,
    wellDiameterMm: 11,
    wellDepthMm: 27,
    liquidCapable: true,
    color: "#dfebe1",
    tags: ["reservoir", "reagent", "wash", "buffer"]
  },
  {
    id: "carrier-nest",
    name: "Carrier Nest",
    profile: "nest",
    componentRole: "nest",
    rows: 0,
    cols: 0,
    wellShape: "none",
    capacityUl: 0,
    maxVolumeUl: 0,
    deadVolumeUl: 0,
    physicalWidthMm: 140,
    physicalDepthMm: 90,
    physicalHeightMm: 18,
    pitchXMm: 0,
    pitchYMm: 0,
    wellDiameterMm: 0,
    wellDepthMm: 0,
    liquidCapable: false,
    color: "#e7e2d7",
    tags: ["carrier", "nest", "adapter", "support"]
  }
];


export default function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const loadRequestRef = useRef(0);
  const [samples, setSamples] = useState<SampleDataset[]>([]);
  const [selectedSampleId, setSelectedSampleId] = useState("");
  const [artifacts, setArtifacts] = useState<SourceArtifact[]>([]);
  const [status, setStatus] = useState("Loading sample registry");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dropActive, setDropActive] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [staticMode, setStaticMode] = useState(false);
  const [sceneInteractionMode, setSceneInteractionMode] = useState<SceneInteractionMode>("edit");
  const [editorSearch, setEditorSearch] = useState("");
  const [dataSearch, setDataSearch] = useState("");
  const [editorTab, setEditorTab] = useState<EditorTab>("script");
  const [devModeOpen, setDevModeOpen] = useState(false);
  const [devLogs, setDevLogs] = useState<DevLogEntry[]>([]);
  const [assetImageOverrides, setAssetImageOverrides] = useState<Record<string, string>>({});
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [scriptTitleState, dispatchScriptTitle] = useReducer(scriptTitleReducer, INITIAL_SCRIPT_TITLE_STATE);
  const {
    title: scriptTitle,
    draft: scriptTitleDraft,
    editing: titleEditing,
    undoStack: titleUndoStack,
    redoStack: titleRedoStack
  } = scriptTitleState;
  const [scriptCommands, setScriptCommands] = useState<ScriptEditorCommand[]>([]);
  const [selectedScriptCommandId, setSelectedScriptCommandId] = useState("");
  const [draggedScriptCommandId, setDraggedScriptCommandId] = useState<string | null>(null);
  const [dropTargetScriptCommandId, setDropTargetScriptCommandId] = useState<string | null>(null);
  const [placedObjects, setPlacedObjects] = useState<LabwareModel[]>([]);
  const [scenePlacementOverrides, setScenePlacementOverrides] = useState<ScenePlacementOverride[]>([]);
  const [draggedSceneObject, setDraggedSceneObject] = useState<SceneObjectDragPayload | null>(null);
  const [sceneFocus, setSceneFocus] = useState<SceneFocusTarget | null>(null);
  const [selectedToolboxFamily, setSelectedToolboxFamily] = useState<OperationFamily>(COMMAND_TOOLBOX[0].family);
  const [leftPanelWidth, setLeftPanelWidth] = useState(DEFAULT_LEFT_PANEL_WIDTH);
  const [rightPanelWidth, setRightPanelWidth] = useState(DEFAULT_RIGHT_PANEL_WIDTH);
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
  const [meshAssetLookup, setMeshAssetLookup] = useState<MeshAssetLookup>({ status: "idle", foundPaths: [], error: "" });
  const assetImageInputRef = useRef<HTMLInputElement | null>(null);
  const titleInputRef = useRef<HTMLInputElement | null>(null);

  const loadDataset = useCallback(async (dataset: SampleDataset) => {
    const requestId = nextLoadRequestId(loadRequestRef);
    setLoading(true);
    setLoadError(null);
    setPlaying(false);
    setSceneFocus(null);
    setSelectedSampleId(dataset.id);
    setStatus(`Loading ${dataset.name}`);

    try {
      const loaded = await loadSample(dataset);
      if (!isLatestLoadRequest(loadRequestRef, requestId)) return;
      setArtifacts(loaded);
      setActiveIndex(0);
      setStatus(`${dataset.name} loaded (${formatArtifactCount(loaded.length)})`);
    } catch (error) {
      if (!isLatestLoadRequest(loadRequestRef, requestId)) return;
      const message = errorMessageFor(error);
      setLoadError(message);
      setStatus(message);
    } finally {
      if (isLatestLoadRequest(loadRequestRef, requestId)) setLoading(false);
    }
  }, []);

  const importFiles = useCallback(async (files: FileList | File[]) => {
    const fileArray = Array.from(files).filter(Boolean);
    if (!fileArray.length) return;

    const requestId = nextLoadRequestId(loadRequestRef);
    setLoading(true);
    setLoadError(null);
    setPlaying(false);
    setSceneFocus(null);
    setSelectedSampleId("");
    setStatus(`Reading ${formatFileCount(fileArray.length)}`);

    try {
      const loaded = await loadFiles(fileArray);
      if (!isLatestLoadRequest(loadRequestRef, requestId)) return;
      setArtifacts(loaded);
      setActiveIndex(0);
      setStatus(`${formatArtifactCount(loaded.length)} loaded from ${formatFileCount(fileArray.length)}`);
    } catch (error) {
      if (!isLatestLoadRequest(loadRequestRef, requestId)) return;
      const message = errorMessageFor(error);
      setLoadError(message);
      setStatus(message);
    } finally {
      if (isLatestLoadRequest(loadRequestRef, requestId)) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const requestId = nextLoadRequestId(loadRequestRef);

    async function bootstrapSamples() {
      setLoading(true);
      setLoadError(null);
      setStatus("Loading sample registry");

      try {
        const sampleList = await fetchSamples();
        if (cancelled || !isLatestLoadRequest(loadRequestRef, requestId)) return;
        setSamples(sampleList);

        const requestedSampleId = initialSampleIdFromLocation();
        const first = sampleList.find((sample) => sample.id === requestedSampleId) || sampleList[0];
        if (!first) {
          setStatus("No sample registry entries found. Load artifacts manually to begin.");
          return;
        }

        setSelectedSampleId(first.id);
        setStatus(`Loading ${first.name}`);
        const loaded = await loadSample(first);
        if (cancelled || !isLatestLoadRequest(loadRequestRef, requestId)) return;
        setArtifacts(loaded);
        setStatus(`${first.name} loaded (${formatArtifactCount(loaded.length)})`);
      } catch (error) {
        if (cancelled || !isLatestLoadRequest(loadRequestRef, requestId)) return;
        const message = errorMessageFor(error);
        setLoadError(message);
        setStatus(message);
      } finally {
        if (!cancelled && isLatestLoadRequest(loadRequestRef, requestId)) setLoading(false);
      }
    }

    bootstrapSamples();
    return () => {
      cancelled = true;
    };
  }, []);

  const parsedModel = useMemo(() => safeBuildProtocolModel(artifacts), [artifacts]);
  const selectedSample = useMemo(() => samples.find((sample) => sample.id === selectedSampleId) || null, [samples, selectedSampleId]);
  const fallbackScriptTitle = useMemo(() => humanReadableProtocolTitle(parsedModel.name, selectedSample), [parsedModel.name, selectedSample]);
  // Latest-value ref so the artifact-restoration effect can read the current
  // fallback title without listing it as a dependency (which would re-run the
  // editor-state reset on a mere Sample-dropdown change and wipe unsaved edits).
  const fallbackScriptTitleRef = useRef(fallbackScriptTitle);
  fallbackScriptTitleRef.current = fallbackScriptTitle;
  const modelLabware = useMemo(() => [...parsedModel.labware, ...placedObjects], [parsedModel.labware, placedObjects]);
  const scriptCommandsWithValidation = useMemo(
    () => scriptCommands.map((command) => validateScriptEditorCommand(command, modelLabware)),
    [scriptCommands, modelLabware]
  );
  const editedProtocolModel = useMemo(
    () => rebuildProtocolWithScriptCommands({ ...parsedModel, labware: modelLabware }, scriptCommandsWithValidation),
    [parsedModel, modelLabware, scriptCommandsWithValidation]
  );
  const model = useMemo<ProtocolModel>(
    () => ({
      ...editedProtocolModel,
      name: scriptTitle.trim() || fallbackScriptTitle
    }),
    [editedProtocolModel, fallbackScriptTitle, scriptTitle]
  );
  const displayWorktableName = useMemo(() => humanReadableWorktableName(model.worktableName, selectedSample), [model.worktableName, selectedSample]);
  const commandCount = model.commands.length;
  const lastCommandIndex = Math.max(0, commandCount - 1);
  const safeActiveIndex = commandCount ? clampCommandIndex(activeIndex, commandCount) : 0;
  const activeCommand = commandCount ? model.commands[safeActiveIndex] : undefined;
  const scriptValidationIssueCount = scriptCommandsWithValidation.reduce((count, command) => count + (command.enabled ? command.validationMessages.length : 0), 0);
  const scriptPlaybackBlocked = scriptValidationIssueCount > 0;
  const visibleScriptCommands = useMemo(
    () => filterScriptEditorCommands(scriptCommandsWithValidation, editorSearch),
    [scriptCommandsWithValidation, editorSearch]
  );
  const selectedScriptCommand = useMemo(
    () => scriptCommandsWithValidation.find((command) => command.id === selectedScriptCommandId) || scriptCommandsWithValidation[0] || null,
    [scriptCommandsWithValidation, selectedScriptCommandId]
  );
  const protocolStats = useMemo<ProtocolStats>(() => commandStatsFor(model), [model]);
  const meshInventoryItems = useMemo(() => meshInventoryItemsForArtifacts(artifacts), [artifacts]);
  const meshInventory = useMemo<MeshInventorySummary>(
    () => meshInventoryFor(meshInventoryItems, meshAssetLookup),
    [meshInventoryItems, meshAssetLookup]
  );
  const geometryCoverage = useMemo<GeometryCoverageSummary>(() => geometryCoverageFor(model), [model]);
  const workspacePanelStyle: WorkspacePanelStyle = {
    "--left-panel-width": leftPanelCollapsed ? "0px" : `${leftPanelWidth}px`,
    "--right-panel-width": rightPanelCollapsed ? "0px" : `${rightPanelWidth}px`
  };
  const workspaceClassName = [
    "workspace",
    "workspace-editor-layout",
    leftPanelCollapsed ? "is-left-collapsed" : "",
    rightPanelCollapsed ? "is-right-collapsed" : ""
  ].filter(Boolean).join(" ");
  const validationHighlights = useMemo<ValidationHighlight[]>(() => validationHighlightsFor(model), [model]);
  const visibleSourceSummaries = model.sourceSummaries.slice(0, MAX_ARTIFACT_ROWS);
  const hiddenSourceSummaryCount = Math.max(0, model.sourceSummaries.length - visibleSourceSummaries.length);
  const warningPreview = model.warnings.slice(0, 3);
  const filteredCommands = useMemo(() => filterCommands(model.commands, dataSearch), [model.commands, dataSearch]);
  const selectedHardwareAsset = useMemo(
    () => model.hardware.assetDetails.find((asset) => asset.id === selectedAssetId) || null,
    [model.hardware.assetDetails, selectedAssetId]
  );
  const handleSceneFocusTarget = useCallback((focusTarget: SceneFocusTarget | null) => {
    setSceneFocus((current) => (sceneFocusTargetsMatch(current, focusTarget) ? current : focusTarget));
  }, []);
  const handleSelectValidationTarget = useCallback(
    (commandIndex: number | null, focusTarget: SceneFocusTarget) => {
      setPlaying(false);
      if (commandIndex !== null) setActiveIndex(clampCommandIndex(commandIndex, commandCount));
      setSceneFocus(focusTarget);
    },
    [commandCount]
  );
  const handleSceneInteractionModeChange = useCallback((mode: SceneInteractionMode) => {
    setSceneInteractionMode(mode);
    if (mode === "view") setDraggedSceneObject(null);
  }, []);

  useEffect(() => {
    const restoredProject = restoredSimulatorProjectStateFromArtifacts(artifacts, parsedModel.labware);
    const nextTitle = restoredProject.scriptTitle || fallbackScriptTitleRef.current;
    const restoredScriptCommands = restoredProject.scriptCommands;
    const nextScriptCommands = (restoredScriptCommands.length ? restoredScriptCommands : scriptCommandsFromProtocolCommands(parsedModel.commands)).map((command) =>
      validateScriptEditorCommand(command, parsedModel.labware)
    );
    dispatchScriptTitle({ type: "reset", title: nextTitle });
    setScriptCommands(nextScriptCommands);
    setSelectedScriptCommandId(restoredProject.selectedScriptCommandId || nextScriptCommands[0]?.id || "");
    setDraggedScriptCommandId(null);
    setDropTargetScriptCommandId(null);
    setAssetImageOverrides(restoredProject.imageOverrides);
    setPlacedObjects(restoredProject.addedObjects);
    setScenePlacementOverrides(restoredProject.placementOverrides);
    if (restoredProject.speed !== null) setSpeed(restoredProject.speed);
    if (restoredProject.staticMode !== null) setStaticMode(restoredProject.staticMode);
    if (restoredProject.sceneInteractionMode) setSceneInteractionMode(restoredProject.sceneInteractionMode);
    if (restoredProject.editorTab) setEditorTab(restoredProject.editorTab);
    setDraggedSceneObject(null);
    // Intentionally keyed only on artifacts/labware: the editor-state reset must
    // run on a real artifact load, not when fallbackScriptTitle changes from a
    // Sample-dropdown switch (that would discard unsaved edits).
  }, [artifacts, parsedModel.labware]);

  useEffect(() => {
    if (!titleEditing) return;
    window.requestAnimationFrame(() => {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    });
  }, [titleEditing]);

  useEffect(() => {
    const restoredProject = restoredSimulatorProjectStateFromArtifacts(artifacts, parsedModel.labware);
    setActiveIndex(restoredProject.activeIndex ?? 0);
    setPlaying(false);
    setSceneFocus(null);
  }, [artifacts, parsedModel.labware]);

  useEffect(() => {
    setActiveIndex((current) => (commandCount ? clampCommandIndex(current, commandCount) : 0));
    if (commandCount < 2) setPlaying(false);
  }, [commandCount]);

  useEffect(() => {
    if (scriptPlaybackBlocked) setPlaying(false);
  }, [scriptPlaybackBlocked]);

  useEffect(() => {
    if (!playing || commandCount <= 1) return;
    const intervalMs = Math.max(MIN_PLAYBACK_INTERVAL_MS, PLAYBACK_INTERVAL_MS / Math.max(0.25, speed));
    const interval = window.setInterval(() => {
      setActiveIndex((current) => {
        const currentSafeIndex = clampCommandIndex(current, commandCount);
        return currentSafeIndex >= lastCommandIndex ? lastCommandIndex : currentSafeIndex + 1;
      });
    }, intervalMs);
    return () => window.clearInterval(interval);
  }, [playing, commandCount, lastCommandIndex, speed]);

  useEffect(() => {
    // Stop playback once the timeline reaches the final command. Kept out of the
    // interval's setActiveIndex updater so we never trigger one setState from
    // inside another updater.
    if (playing && commandCount > 1 && safeActiveIndex >= lastCommandIndex) setPlaying(false);
  }, [playing, commandCount, safeActiveIndex, lastCommandIndex]);

  useEffect(() => {
    const assetPaths = uniqueStrings(meshInventoryItems.map((mesh) => mesh.assetPath).filter(Boolean));
    if (!assetPaths.length) {
      setMeshAssetLookup({ status: "idle", foundPaths: [], error: "" });
      return;
    }

    let cancelled = false;
    const cachedFoundPaths = cachedFoundModelAssetPaths(assetPaths);
    const pendingAssetPaths = assetPaths.filter((assetPath) => !modelAssetCheckCache.has(normalizeModelAssetPath(assetPath)));
    const cachedMissingCount = assetPaths.length - cachedFoundPaths.length;
    setMeshAssetLookup({
      status: pendingAssetPaths.length ? "checking" : "complete",
      foundPaths: cachedFoundPaths,
      error: pendingAssetPaths.length || cachedMissingCount === 0
        ? ""
        : `${cachedMissingCount} converted model asset${cachedMissingCount === 1 ? "" : "s"} not found`
    });

    async function checkMeshAssets() {
      await checkModelAssetsThrottled(pendingAssetPaths);
      if (cancelled) return;
      const foundPaths = cachedFoundModelAssetPaths(assetPaths);
      const missingCount = assetPaths.length - foundPaths.length;
      setMeshAssetLookup({
        status: "complete",
        foundPaths,
        error: missingCount === 0 ? "" : `${missingCount} converted model asset${missingCount === 1 ? "" : "s"} not found`
      });
    }

    if (pendingAssetPaths.length) void checkMeshAssets();
    return () => {
      cancelled = true;
    };
  }, [meshInventoryItems]);

  useEffect(() => {
    const originalConsole = {
      log: console.log,
      info: console.info,
      warn: console.warn,
      error: console.error
    };

    function appendDevLog(level: DevLogLevel, values: unknown[]) {
      const entry: DevLogEntry = {
        id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
        level,
        timestamp: new Date().toLocaleTimeString(),
        message: values.map(formatDevLogValue).join(" ")
      };
      setDevLogs((current) => [...current, entry].slice(-MAX_DEV_LOGS));
    }

    (console.log as (...values: unknown[]) => void) = (...values: unknown[]) => {
      appendDevLog("log", values);
      originalConsole.log(...values);
    };
    (console.info as (...values: unknown[]) => void) = (...values: unknown[]) => {
      appendDevLog("info", values);
      originalConsole.info(...values);
    };
    (console.warn as (...values: unknown[]) => void) = (...values: unknown[]) => {
      appendDevLog("warn", values);
      originalConsole.warn(...values);
    };
    (console.error as (...values: unknown[]) => void) = (...values: unknown[]) => {
      appendDevLog("error", values);
      originalConsole.error(...values);
    };

    function handleWindowError(event: ErrorEvent) {
      appendDevLog("error", [event.message, event.filename, event.lineno]);
    }

    function handleUnhandledRejection(event: PromiseRejectionEvent) {
      appendDevLog("error", ["Unhandled promise rejection", event.reason]);
    }

    function handleDevShortcut(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName.toLowerCase();
      const isTyping = tagName === "input" || tagName === "textarea" || tagName === "select" || target?.isContentEditable;
      if (!isTyping && (event.key === "`" || event.key === "~")) {
        event.preventDefault();
        setDevModeOpen((current) => !current);
      }
      if (event.key === "Escape") setDevModeOpen(false);
    }

    window.addEventListener("error", handleWindowError);
    window.addEventListener("unhandledrejection", handleUnhandledRejection);
    window.addEventListener("keydown", handleDevShortcut);

    return () => {
      console.log = originalConsole.log;
      console.info = originalConsole.info;
      console.warn = originalConsole.warn;
      console.error = originalConsole.error;
      window.removeEventListener("error", handleWindowError);
      window.removeEventListener("unhandledrejection", handleUnhandledRejection);
      window.removeEventListener("keydown", handleDevShortcut);
    };
  }, []);

  useEffect(() => {
    function handleHistoryShortcut(event: KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || isEditableShortcutTarget(event.target)) return;
      const key = event.key.toLowerCase();
      const isUndo = key === "z" && !event.shiftKey;
      const isRedo = key === "y" || (key === "z" && event.shiftKey);
      if (!isUndo && !isRedo) return;

      event.preventDefault();
      if (isUndo) handleUndoTitle();
      if (isRedo) handleRedoTitle();
    }

    window.addEventListener("keydown", handleHistoryShortcut);
    return () => window.removeEventListener("keydown", handleHistoryShortcut);
  }, [titleUndoStack, titleRedoStack, scriptTitle, fallbackScriptTitle]);

  function currentScriptTitle(): string {
    return scriptTitle.trim() || fallbackScriptTitle;
  }

  function beginTitleEdit() {
    if (!artifacts.length && parsedModel.id === "empty") return;
    const title = currentScriptTitle();
    dispatchScriptTitle({ type: "begin-edit", title });
  }

  function commitScriptTitle(value: string) {
    const nextTitle = sanitizeScriptTitle(value) || currentScriptTitle();
    const previousTitle = currentScriptTitle();
    dispatchScriptTitle({ type: "commit", title: nextTitle, currentTitle: previousTitle });
    if (nextTitle === previousTitle) return;

    setStatus(`Renamed script to ${nextTitle}`);
  }

  function cancelTitleEdit() {
    dispatchScriptTitle({ type: "cancel-edit", title: currentScriptTitle() });
  }

  function handleTitleKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitScriptTitle(scriptTitleDraft);
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cancelTitleEdit();
    }
  }

  function handleUndoTitle() {
    if (!titleUndoStack.length) return;
    const previousTitle = titleUndoStack[titleUndoStack.length - 1];
    const currentTitle = currentScriptTitle();
    dispatchScriptTitle({ type: "undo", currentTitle });
    setStatus(`Undo: ${previousTitle}`);
  }

  function handleRedoTitle() {
    if (!titleRedoStack.length) return;
    const nextTitle = titleRedoStack[0];
    const currentTitle = currentScriptTitle();
    dispatchScriptTitle({ type: "redo", currentTitle });
    setStatus(`Redo: ${nextTitle}`);
  }

  function handleSampleChange(event: ChangeEvent<HTMLSelectElement>) {
    const nextSampleId = event.target.value;
    const dataset = samples.find((sample) => sample.id === nextSampleId);
    if (!dataset) {
      setSelectedSampleId("");
      setPlaying(false);
      setSceneFocus(null);
      if (!artifacts.length) setStatus("No uploaded artifacts loaded");
      return;
    }
    void loadDataset(dataset);
  }

  async function handleFileSelection(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (!files?.length) return;
    await importFiles(files);
    event.target.value = "";
  }

  async function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    setDropActive(false);
    if (!event.dataTransfer.files.length) return;
    await importFiles(event.dataTransfer.files);
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (!dropActive) setDropActive(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (event.currentTarget === event.target) setDropActive(false);
  }

  function handleObjectLibraryDragStart(event: DragEvent<HTMLElement>, item: ObjectLibraryItem) {
    if (sceneInteractionMode !== "edit") {
      event.preventDefault();
      setDraggedSceneObject(null);
      setStatus("Switch to Edit mode to place objects on the 3D deck");
      return;
    }
    const payload: SceneObjectDragPayload = { item, startedAt: Date.now() };
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/x-tecan-scene-object", item.id);
    event.dataTransfer.setData("text/plain", item.name);
    setDraggedSceneObject(payload);
    setStatus(`Drag ${item.name} onto an open deck snap pad`);
  }

  function handleObjectLibraryDragEnd() {
    setDraggedSceneObject(null);
  }

  function handleSceneObjectDrop(payload: SceneObjectDragPayload, placement: SceneObjectDropPlacement) {
    const dropped = labwareFromObjectLibraryDrop(payload.item, placement, [...parsedModel.labware, ...placedObjects]);
    setPlacedObjects((current) => [...current, dropped]);
    setScenePlacementOverrides((current) =>
      upsertScenePlacementOverride(current, {
        id: dropped.id,
        label: dropped.label,
        labwareLabel: dropped.label,
        position: placement.position,
        rotationY: placement.rotationY,
        source: "user-drag",
        snapTarget: placement.snapTarget,
        updatedAt: new Date().toISOString()
      })
    );
    setDraggedSceneObject(null);
    handleSceneFocusTarget({
      kind: "editor-object",
      label: dropped.label,
      labwareLabel: dropped.label,
      slotIndex: dropped.slotIndex
    });
    setStatus(`Added ${dropped.label} at ${placement.snapTarget?.label || `snap target ${placement.slotIndex + 1}`}`);
  }

  function handleScenePlacementOverride(override: ScenePlacementOverride) {
    setScenePlacementOverrides((current) => upsertScenePlacementOverride(current, override));
    setStatus(`Moved ${override.label}`);
  }

  function handlePanelResizeStart(side: SidePanel, event: ReactPointerEvent<HTMLDivElement>) {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    event.preventDefault();
    const rect = workspace.getBoundingClientRect();
    const setWidth = side === "left" ? setLeftPanelWidth : setRightPanelWidth;
    if (side === "left") setLeftPanelCollapsed(false);
    else setRightPanelCollapsed(false);
    document.body.classList.add("is-resizing-panel");

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const rawWidth = side === "left"
        ? moveEvent.clientX - rect.left - WORKSPACE_PADDING_PX
        : rect.right - moveEvent.clientX - WORKSPACE_PADDING_PX;
      setWidth(clampNumber(rawWidth, MIN_SIDE_PANEL_WIDTH, MAX_SIDE_PANEL_WIDTH));
    };

    const handlePointerUp = () => {
      document.body.classList.remove("is-resizing-panel");
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp, { once: true });
  }

  function handleAddScriptCommand() {
    const nextCommand = validateScriptEditorCommand(createManualScriptCommand(scriptCommands.length), modelLabware);
    setScriptCommands((current) => [...current, nextCommand]);
    setSelectedScriptCommandId(nextCommand.id);
    setEditorTab("script");
    setPlaying(false);
    setStatus(`Added ${nextCommand.name}`);
  }

  function handleAddToolboxCommand(template: CommandToolboxTemplate) {
    const nextCommand = validateScriptEditorCommand(createScriptCommandFromTemplate(template), modelLabware);
    setScriptCommands((current) => [...current, nextCommand]);
    setSelectedScriptCommandId(nextCommand.id);
    setEditorTab("script");
    setPlaying(false);
    setStatus(`Added ${nextCommand.name}`);
  }

  function handleSelectScriptCommand(command: ScriptEditorCommand) {
    setSelectedScriptCommandId(command.id);
    const modelIndex = modelCommandIndexForScriptCommand(model.commands, command);
    if (modelIndex >= 0) {
      const selectedCommand = model.commands[modelIndex];
      setActiveIndex(modelIndex);
      setSceneFocus(commandFocusTarget(selectedCommand));
    }
  }

  function handleUpdateScriptCommand(commandId: string, patch: ScriptCommandPatch) {
    setScriptCommands((current) =>
      current.map((command) =>
        command.id === commandId
          ? validateScriptEditorCommand(
              {
                ...command,
                ...patch,
                family: patch.operation && !patch.family ? familyForEditorOperation(patch.operation) : patch.family || command.family,
                dirty: true
              },
              modelLabware
            )
          : command
      )
    );
    setPlaying(false);
  }

  function handleDuplicateScriptCommand(commandId: string) {
    const source = scriptCommandsWithValidation.find((command) => command.id === commandId);
    if (!source) return;
    const duplicate = validateScriptEditorCommand(
      {
        ...source,
        id: uniqueScriptCommandId("duplicate"),
        sourceCommandId: undefined,
        name: `${source.name || "Command"} copy`,
        enabled: true,
        dirty: true,
        validationIssues: [],
        validationMessages: []
      },
      modelLabware
    );
    setScriptCommands((current) => insertAfterId(current, commandId, duplicate));
    setSelectedScriptCommandId(duplicate.id);
    setPlaying(false);
    setStatus(`Duplicated ${source.name || "command"}`);
  }

  function handleDeleteScriptCommand(commandId: string) {
    const command = scriptCommands.find((item) => item.id === commandId);
    const remaining = scriptCommands.filter((item) => item.id !== commandId);
    setScriptCommands(remaining);
    if (selectedScriptCommandId === commandId) setSelectedScriptCommandId(remaining[0]?.id || "");
    setPlaying(false);
    setStatus(command ? `Deleted ${command.name || "command"}` : "Deleted command");
  }

  function handleScriptCommandDragStart(event: DragEvent<HTMLElement>, commandId: string) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-tecan-script-command", commandId);
    installScriptCommandDragGhost(event);
    setDraggedScriptCommandId(commandId);
  }

  function handleToolboxCommandDragStart(event: DragEvent<HTMLElement>, template: CommandToolboxTemplate) {
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/x-tecan-command-template", template.id);
    event.dataTransfer.setData("text/plain", template.name);
    setStatus(`Drag ${template.name} into the script editor`);
  }

  function handleScriptCommandDragOver(event: DragEvent<HTMLElement>, commandId: string) {
    event.preventDefault();
    const isTemplateDrag = dataTransferHasType(event.dataTransfer, "application/x-tecan-command-template");
    event.dataTransfer.dropEffect = isTemplateDrag ? "copy" : "move";
    if ((draggedScriptCommandId && draggedScriptCommandId !== commandId) || isTemplateDrag) setDropTargetScriptCommandId(commandId);
  }

  function handleScriptCommandDrop(event: DragEvent<HTMLElement>, targetCommandId: string) {
    event.preventDefault();
    event.stopPropagation();
    const templateId = event.dataTransfer.getData("application/x-tecan-command-template");
    if (templateId) {
      const template = commandToolboxTemplateById(templateId);
      setDraggedScriptCommandId(null);
      setDropTargetScriptCommandId(null);
      if (!template) return;
      const nextCommand = validateScriptEditorCommand(createScriptCommandFromTemplate(template), modelLabware);
      setScriptCommands((current) => insertBeforeId(current, targetCommandId, nextCommand));
      setSelectedScriptCommandId(nextCommand.id);
      setEditorTab("script");
      setPlaying(false);
      setStatus(`Inserted ${nextCommand.name}`);
      return;
    }
    const sourceCommandId = draggedScriptCommandId || event.dataTransfer.getData("application/x-tecan-script-command");
    setDraggedScriptCommandId(null);
    setDropTargetScriptCommandId(null);
    if (!sourceCommandId || sourceCommandId === targetCommandId) return;
    setScriptCommands((current) => moveItemBeforeId(current, sourceCommandId, targetCommandId));
    setSelectedScriptCommandId(sourceCommandId);
    setPlaying(false);
  }

  function handleScriptCommandListDragOver(event: DragEvent<HTMLElement>) {
    if (!dataTransferHasType(event.dataTransfer, "application/x-tecan-command-template")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleScriptCommandListDrop(event: DragEvent<HTMLElement>) {
    const templateId = event.dataTransfer.getData("application/x-tecan-command-template");
    if (!templateId) return;
    event.preventDefault();
    const template = commandToolboxTemplateById(templateId);
    setDropTargetScriptCommandId(null);
    if (!template) return;
    const nextCommand = validateScriptEditorCommand(createScriptCommandFromTemplate(template), modelLabware);
    setScriptCommands((current) => [...current, nextCommand]);
    setSelectedScriptCommandId(nextCommand.id);
    setEditorTab("script");
    setPlaying(false);
    setStatus(`Added ${nextCommand.name}`);
  }

  function handleScriptCommandDragEnd() {
    setDraggedScriptCommandId(null);
    setDropTargetScriptCommandId(null);
  }

  async function handleExportImageBundle() {
    try {
      const bundle = await buildReplacementImageBundle(assetImageOverrides, model.hardware.assetDetails);
      if (!bundle) {
        setStatus("No replacement images to bundle");
        return;
      }
      downloadBlob(
        `${modelAssetSlug(model.name || "protocol")}-replacement-images-${new Date().toISOString().slice(0, 10)}.zip`,
        bundle,
        "application/zip"
      );
      setStatus(`Exported ${Object.keys(assetImageOverrides).length.toLocaleString()} replacement image${Object.keys(assetImageOverrides).length === 1 ? "" : "s"} as a bundle`);
    } catch (error) {
      setStatus(`Image bundle export failed: ${errorMessageFor(error)}`);
    }
  }

  function handleTogglePlayback() {
    if (commandCount < 2) return;
    if (scriptPlaybackBlocked) {
      setPlaying(false);
      setStatus(`Fix ${scriptValidationIssueCount} script validation issue${scriptValidationIssueCount === 1 ? "" : "s"} before playback`);
      return;
    }
    if (!playing && safeActiveIndex >= lastCommandIndex) setActiveIndex(0);
    setSceneFocus(null);
    setPlaying((current) => !current);
  }

  function handleResetTimeline() {
    setPlaying(false);
    setSceneFocus(null);
    setActiveIndex(0);
  }

  function handleTimelineChange(event: ChangeEvent<HTMLInputElement>) {
    const nextIndex = clampCommandIndex(Number(event.target.value), commandCount);
    const command = commandForIndex(model.commands, nextIndex);
    setPlaying(false);
    setActiveIndex(nextIndex);
    if (command) {
      selectScriptCommandForModelCommand(command);
      setSceneFocus(commandFocusTarget(command));
    } else {
      setSceneFocus(null);
    }
  }

  function handleCommandSelect(command: CommandModel, index: number) {
    setPlaying(false);
    setActiveIndex(clampCommandIndex(index, commandCount));
    selectScriptCommandForModelCommand(command);
    setSceneFocus(commandFocusTarget(command));
  }

  function selectScriptCommandForModelCommand(command: CommandModel) {
    const scriptCommand = scriptCommandForModelCommand(scriptCommandsWithValidation, command);
    if (scriptCommand) setSelectedScriptCommandId(scriptCommand.id);
  }

  function handleSave() {
    try {
      const saved = buildSavedScriptArtifact(artifacts, model, selectedSampleId, assetImageOverrides, scriptCommandsWithValidation, scenePlacementOverrides, placedObjects, {
        editorTab,
        selectedScriptCommandId: selectedScriptCommand?.id || "",
        activeIndex: safeActiveIndex,
        speed,
        staticMode,
        sceneInteractionMode
      });
      downloadText(saved.fileName, saved.body, saved.mimeType);
      setStatus(`Saved ${saved.fileName}`);
    } catch (error) {
      setStatus(`Save failed: ${errorMessageFor(error)}`);
    }
  }

  function handleExport() {
    try {
    const exportedAt = new Date().toISOString();
    const payload = {
      exportedAt,
      app: "tecan-protocol-simulator",
      kind: "simulator-project",
      schemaVersion: 2,
      sampleId: selectedSampleId || null,
      status,
      playback: {
        activeIndex: safeActiveIndex,
        speed,
        staticMode,
        sceneInteractionMode
      },
      protocol: model,
      editor: {
        version: 1,
        scriptCommands: scriptCommandsWithValidation,
        addedObjects: addedObjectsForExport(model, placedObjects),
        imageOverrides: imageOverridesForExport(assetImageOverrides, model.hardware.assetDetails),
        placementOverrides: scenePlacementOverrides,
        ui: {
          editorTab,
          selectedScriptCommandId: selectedScriptCommand?.id || "",
          scriptTitle: model.name,
          sceneInteractionMode
        },
        notes: []
      },
      artifacts: artifacts.map((artifact) => ({
        id: artifact.id,
        name: artifact.name,
        kind: artifact.kind,
        source: artifact.source,
        path: artifact.path,
        size: artifact.size,
        mimeType: artifact.mimeType,
        meshGuid: artifact.meshGuid,
        meshName: artifact.meshName,
        meshSourcePath: artifact.meshSourcePath,
        meshPayloadSize: artifact.meshPayloadSize,
        meshDecodedSize: artifact.meshDecodedSize,
        modelAssetPath: artifact.modelAssetPath,
        modelAssetFormat: artifact.modelAssetFormat
      })),
      assetImageOverrides: imageOverridesForExport(assetImageOverrides, model.hardware.assetDetails),
      generatedAssetBundle: replacementImageBundleManifest(assetImageOverrides, model.hardware.assetDetails)
    };
    downloadText(
      `${modelAssetSlug(model.name || "protocol")}-simulator-project-${exportedAt.slice(0, 10)}.json`,
      JSON.stringify(payload, null, 2),
      "application/json"
    );
    setStatus(`Exported simulator project at ${new Date(exportedAt).toLocaleTimeString()}`);
    } catch (error) {
      setStatus(`Export failed: ${errorMessageFor(error)}`);
    }
  }

  function handleAssetImageSelection(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !selectedHardwareAsset) return;
    if (!file.type.startsWith("image/")) {
      setStatus("Selected file was not an image.");
      event.target.value = "";
      return;
    }

    // Capture the target asset now so a late onload always applies to the asset
    // that was selected when the read started, even if the user navigates away.
    const targetAsset = selectedHardwareAsset;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : "";
      if (!dataUrl) return;
      setAssetImageOverrides((current) => ({ ...current, [targetAsset.id]: dataUrl }));
      setStatus(`Updated image for ${targetAsset.name}`);
    };
    reader.onerror = () => setStatus(`Could not read ${file.name}`);
    reader.readAsDataURL(file);
    event.target.value = "";
  }

  async function handleCopyDevLogs() {
    const text = devLogs.length ? devLogs.map(formatDevLogEntry).join("\n") : "No dev logs captured yet.";
    try {
      await navigator.clipboard.writeText(text);
      setStatus(`Copied ${devLogs.length.toLocaleString()} dev log${devLogs.length === 1 ? "" : "s"} to clipboard`);
    } catch {
      setStatus("Clipboard access failed. Select and copy from the dev panel manually.");
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark">
            <FlaskConical size={22} />
          </div>
          <div>
            {titleEditing ? (
              <input
                ref={titleInputRef}
                className="script-title-input"
                value={scriptTitleDraft}
                onChange={(event) => dispatchScriptTitle({ type: "set-draft", title: event.target.value })}
                onBlur={() => commitScriptTitle(scriptTitleDraft)}
                onKeyDown={handleTitleKeyDown}
                aria-label="Script title"
              />
            ) : (
              <button className="script-title-button" type="button" onClick={beginTitleEdit} title="Rename script">
                <h1>{model.name}</h1>
              </button>
            )}
            <p>{displayWorktableName}</p>
          </div>
        </div>

        <div className="topbar-controls">
          <button className="icon-button" type="button" onClick={handleUndoTitle} title="Undo (Ctrl+Z / Cmd+Z)" aria-label="Undo title change" disabled={!titleUndoStack.length}>
            <Undo2 size={18} />
          </button>
          <button className="icon-button" type="button" onClick={handleRedoTitle} title="Redo (Ctrl+Y / Ctrl+Shift+Z / Cmd+Shift+Z)" aria-label="Redo title change" disabled={!titleRedoStack.length}>
            <Redo2 size={18} />
          </button>
          <label className="sample-select">
            <span>Sample</span>
            <select value={selectedSampleId} onChange={handleSampleChange} disabled={loading || !samples.length}>
              <option value="">Uploaded artifacts</option>
              {samples.map((sample) => (
                <option key={sample.id} value={sample.id}>
                  {sample.name}
                </option>
              ))}
            </select>
          </label>

          <button className="icon-button with-label" type="button" onClick={handleSave} title="Save renamed script" disabled={!artifacts.length}>
            <Save size={18} />
            <span>Save</span>
          </button>
          <button className="icon-button with-label" type="button" onClick={() => fileInputRef.current?.click()} title="Import artifacts" disabled={loading}>
            <Upload size={18} />
            <span>{loading ? "Importing" : "Import"}</span>
          </button>
          <button className="icon-button with-label" type="button" onClick={handleExport} title="Export simulator project package" disabled={!artifacts.length}>
            <FileArchive size={18} />
            <span>Export</span>
          </button>
          <input
            ref={fileInputRef}
            className="hidden-input"
            type="file"
            multiple
            accept={ACCEPTED_ARTIFACT_EXTENSIONS}
            onChange={handleFileSelection}
          />
          <input ref={assetImageInputRef} className="hidden-input" type="file" accept="image/*" onChange={handleAssetImageSelection} />
        </div>
      </header>

      <main ref={workspaceRef} className={workspaceClassName} style={workspacePanelStyle}>
        <aside className="left-panel editor-panel">
          <button
            className="panel-bookmark panel-bookmark-left"
            type="button"
            onClick={() => setLeftPanelCollapsed((collapsed) => !collapsed)}
            title={leftPanelCollapsed ? "Expand left panel" : "Collapse left panel"}
            aria-label={leftPanelCollapsed ? "Expand left panel" : "Collapse left panel"}
          >
            <span>{leftPanelCollapsed ? "Commands" : "‹"}</span>
          </button>
          <div className="left-panel-scroll">
            <section className="panel-section editor-shell">
              <div className="section-heading">
                <Layers3 size={17} />
                <h2>Editing</h2>
              </div>

              <details className="command-toolbox-panel command-toolbox-accordion" open>
                <summary className="section-heading">
                  <ListChecks size={17} />
                  <h3>Command Toolbox</h3>
                </summary>
                <div className="command-toolbox-accordion-body">
                  <CommandToolbox
                    categories={COMMAND_TOOLBOX}
                    selectedFamily={selectedToolboxFamily}
                    onSelectFamily={setSelectedToolboxFamily}
                    onAddCommand={handleAddToolboxCommand}
                    onDragStart={handleToolboxCommandDragStart}
                  />
                </div>
              </details>

              <label className="panel-search">
                <span>Search editing tools</span>
                <input
                  type="search"
                  value={editorSearch}
                  onChange={(event) => setEditorSearch(event.target.value)}
                  placeholder="Search script commands or objects"
                />
              </label>

              <div className="editor-tabs" role="tablist" aria-label="Simulator editing tools">
                <button className={editorTab === "script" ? "is-active" : ""} type="button" onClick={() => setEditorTab("script")}>
                  Script Editor
                </button>
                <button className={editorTab === "objects" ? "is-active" : ""} type="button" onClick={() => setEditorTab("objects")}>
                  Add Object
                </button>
              </div>

              {editorTab === "script" ? (
                <ScriptEditorPanel
                  commands={visibleScriptCommands}
                  allCommands={scriptCommandsWithValidation}
                  selectedCommandId={selectedScriptCommand?.id || ""}
                  draggedCommandId={draggedScriptCommandId}
                  dropTargetCommandId={dropTargetScriptCommandId}
                  labware={modelLabware}
                  validationIssueCount={scriptValidationIssueCount}
                  onAdd={handleAddScriptCommand}
                  onSelect={handleSelectScriptCommand}
                  onUpdate={handleUpdateScriptCommand}
                  onDuplicate={handleDuplicateScriptCommand}
                  onDelete={handleDeleteScriptCommand}
                  onDragStart={handleScriptCommandDragStart}
                  onDragOver={handleScriptCommandDragOver}
                  onDrop={handleScriptCommandDrop}
                  onDragEnd={handleScriptCommandDragEnd}
                  onListDragOver={handleScriptCommandListDragOver}
                  onListDrop={handleScriptCommandListDrop}
                />
              ) : (
                <ObjectLibraryPanel
                  items={filterObjectLibrary(OBJECT_LIBRARY, editorSearch)}
                  activeItemId={draggedSceneObject?.item.id || ""}
                  enabled={sceneInteractionMode === "edit"}
                  onDragStart={handleObjectLibraryDragStart}
                  onDragEnd={handleObjectLibraryDragEnd}
                />
              )}
            </section>

            <section className={`panel-section drop-section ${dropActive ? "is-dragging" : ""}`} onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}>
              <div className="drop-target">
                <Upload size={20} />
                <span>{dropActive ? "Release to import artifacts" : "Drop IR, XSCR, GWL, ZEIA, geometry files, simulation JSON, or hardware images"}</span>
              </div>
            </section>
          </div>
        </aside>

        {!leftPanelCollapsed && (
          <div
            className="panel-resizer panel-resizer-left"
            role="separator"
            aria-label="Resize left panel"
            aria-orientation="vertical"
            onPointerDown={(event) => handlePanelResizeStart("left", event)}
          />
        )}

        <section className={`scene-panel scene-mode-${sceneInteractionMode} ${draggedSceneObject ? "is-object-dragging" : ""}`}>
          <Suspense fallback={<DeckSceneFallback />}>
            <DeckScene
              model={model}
              activeIndex={safeActiveIndex}
              focusTarget={sceneFocus}
              validationHighlights={validationHighlights}
              interactionMode={sceneInteractionMode}
              staticMode={staticMode}
              onFocusTarget={handleSceneFocusTarget}
              draggedObject={draggedSceneObject}
              onObjectDrop={handleSceneObjectDrop}
              placementOverrides={scenePlacementOverrides}
              onPlacementOverride={handleScenePlacementOverride}
            />
          </Suspense>
          <div className="scene-mode-toggle" role="group" aria-label="3D simulator mode">
            <button
              className={sceneInteractionMode === "view" ? "is-active" : ""}
              type="button"
              onClick={() => handleSceneInteractionModeChange("view")}
              title="View mode"
              aria-pressed={sceneInteractionMode === "view"}
            >
              <Eye size={15} />
              <span>View</span>
            </button>
            <button
              className={sceneInteractionMode === "edit" ? "is-active" : ""}
              type="button"
              onClick={() => handleSceneInteractionModeChange("edit")}
              title="Edit mode"
              aria-pressed={sceneInteractionMode === "edit"}
            >
              <Pencil size={15} />
              <span>Edit</span>
            </button>
          </div>
          <div className="scene-status">
            <span>{loadError ? `Error: ${loadError}` : status}</span>
            <span>{sceneFocus ? `Focus: ${sceneFocus.label}` : `${model.context} · ${sceneInteractionMode} mode${staticMode ? " · static mode" : " · realtime mode"}`}</span>
          </div>
        </section>

        {!rightPanelCollapsed && (
          <div
            className="panel-resizer panel-resizer-right"
            role="separator"
            aria-label="Resize right panel"
            aria-orientation="vertical"
            onPointerDown={(event) => handlePanelResizeStart("right", event)}
          />
        )}

        <aside className="right-panel data-panel">
          <button
            className="panel-bookmark panel-bookmark-right"
            type="button"
            onClick={() => setRightPanelCollapsed((collapsed) => !collapsed)}
            title={rightPanelCollapsed ? "Expand right panel" : "Collapse right panel"}
            aria-label={rightPanelCollapsed ? "Expand right panel" : "Collapse right panel"}
          >
            <span>{rightPanelCollapsed ? "Data" : "›"}</span>
          </button>
          <section className="panel-section timeline-controls">
            <div className="playback-row">
              <button
                className="icon-button"
                type="button"
                onClick={handleTogglePlayback}
                title={playing ? "Pause" : "Play"}
                aria-label={playing ? "Pause playback" : "Play playback"}
                disabled={commandCount < 2 || loading || scriptPlaybackBlocked}
              >
                {playing ? <Pause size={18} /> : <Play size={18} />}
              </button>
              <button className="icon-button" type="button" onClick={handleResetTimeline} title="Reset timeline" aria-label="Reset timeline" disabled={commandCount < 1}>
                <RotateCcw size={18} />
              </button>
              <input
                aria-label="Command timeline"
                className="timeline-slider"
                type="range"
                min="0"
                max={lastCommandIndex}
                value={safeActiveIndex}
                onChange={handleTimelineChange}
                disabled={commandCount < 2}
              />
              <select className="speed-select" value={speed} aria-label="Playback speed" onChange={(event) => setSpeed(Number(event.target.value))} disabled={commandCount < 2}>
                {SPEEDS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <label className="static-toggle">
              <input type="checkbox" checked={staticMode} onChange={(event) => setStaticMode(event.target.checked)} />
              <span>Static animation mode</span>
            </label>
          </section>

          <section className="panel-section active-command">
            <div className="section-heading">
              <TestTube2 size={17} />
              <h2>Active Command</h2>
            </div>
            {activeCommand ? <CommandDetails command={activeCommand} activeIndex={safeActiveIndex} total={commandCount} /> : <p className="muted">No command selected</p>}
          </section>

          <details className="panel-section command-list-section timeline-accordion" open>
            <summary className="section-heading">
              <h2>Timeline</h2>
            </summary>
            <div className="timeline-accordion-body">
              <label className="panel-search">
                <span>Search commands and data</span>
                <input
                  type="search"
                  value={dataSearch}
                  onChange={(event) => setDataSearch(event.target.value)}
                  placeholder="Search timeline, labware, operation"
                />
              </label>
              <TimelineCommandList commands={filteredCommands} totalCommandCount={model.commands.length} activeIndex={safeActiveIndex} onSelect={handleCommandSelect} />
            </div>
          </details>

          <AccordionSection title="Data / Diagnostics" icon={<FileArchive size={17} />} defaultOpen>
            <AccordionSection title="Protocol" icon={<Layers3 size={16} />} defaultOpen>
              <ProtocolOverviewPanel stats={protocolStats} warnings={warningPreview} hiddenWarningCount={Math.max(0, model.warnings.length - warningPreview.length)} />
            </AccordionSection>
            <AccordionSection title="ZEIA Context" icon={<Box size={16} />} lazyMount>
              <ZeiaContextPanel
                model={model}
                imageOverrides={assetImageOverrides}
                onPreviewAsset={(assetId) => setSelectedAssetId(assetId)}
                onExportImageBundle={handleExportImageBundle}
              />
            </AccordionSection>
            <AccordionSection title="Mesh Inventory" icon={<Box size={16} />} lazyMount>
              <MeshInventoryPanel inventory={meshInventory} coverage={geometryCoverage} />
            </AccordionSection>
            <AccordionSection title="Source Artifacts" icon={<FileArchive size={16} />} lazyMount>
              <SourceSummariesPanel summaries={visibleSourceSummaries} hiddenCount={hiddenSourceSummaryCount} />
            </AccordionSection>
            <AccordionSection title="Readiness" icon={<CheckCircle2 size={16} />} lazyMount>
              <ReadinessPanel model={model} />
            </AccordionSection>
            <AccordionSection title="Validation Gates" icon={<ListChecks size={16} />} lazyMount>
              <ValidationGatesPanel model={model} onSelectTarget={handleSelectValidationTarget} />
            </AccordionSection>
            <AccordionSection title="Alias Repairs / Worktable Diff" icon={<GitCompareArrows size={16} />} lazyMount>
              <WorktableDiffPanel repairs={model.repairs} model={model} onSelectTarget={handleSelectValidationTarget} />
            </AccordionSection>
          </AccordionSection>
        </aside>
      </main>

      {selectedHardwareAsset && (
        <HardwareImageModal
          asset={selectedHardwareAsset}
          imageSrc={assetImageOverrides[selectedHardwareAsset.id] || selectedHardwareAsset.imageSrc || ""}
          onChangeImage={() => assetImageInputRef.current?.click()}
          onClose={() => setSelectedAssetId(null)}
        />
      )}

      {devModeOpen && <DevConsole logs={devLogs} onClose={() => setDevModeOpen(false)} onCopy={handleCopyDevLogs} />}
    </div>
  );
}

function initialSampleIdFromLocation(): string {
  try {
    return new URLSearchParams(window.location.search).get("sample") || "";
  } catch {
    return "";
  }
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function CommandToolbox({
  categories,
  selectedFamily,
  onSelectFamily,
  onAddCommand,
  onDragStart
}: {
  categories: CommandToolboxCategory[];
  selectedFamily: OperationFamily;
  onSelectFamily: (family: OperationFamily) => void;
  onAddCommand: (template: CommandToolboxTemplate) => void;
  onDragStart: (event: DragEvent<HTMLElement>, template: CommandToolboxTemplate) => void;
}) {
  const selectedCategory = categories.find((category) => category.family === selectedFamily) || categories[0];
  const [commandSearch, setCommandSearch] = useState("");
  const normalizedCommandSearch = normalizeSearchText(commandSearch);
  const allCommandTemplates = categories.flatMap((category) => category.commands.map((template) => ({ category, template })));
  const visibleCommandTemplates = normalizedCommandSearch
    ? allCommandTemplates.filter(({ category, template }) =>
        normalizeSearchText([category.label, template.name, template.operation, template.description, template.family].join(" ")).includes(normalizedCommandSearch)
      )
    : selectedCategory.commands.map((template) => ({ category: selectedCategory, template }));

  return (
    <div className="command-toolbox">
      <label className="command-toolbox-search panel-search">
        <span>Search general commands</span>
        <input
          type="search"
          value={commandSearch}
          onChange={(event) => setCommandSearch(event.target.value)}
          placeholder="Search comment, user prompt, get tip..."
        />
      </label>

      <div className="toolbox-category-grid" role="tablist" aria-label="Command toolbox categories">
        {categories.map((category) => (
          <button
            key={category.family}
            className={`toolbox-category-card ${category.family === selectedCategory.family ? "is-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={category.family === selectedCategory.family}
            onClick={() => onSelectFamily(category.family)}
          >
            <span className={`command-family family-${category.family}`}>{category.label}</span>
            <strong>{category.commands.length.toLocaleString()}</strong>
          </button>
        ))}
      </div>

      <div className="toolbox-category-detail">
        <div className="toolbox-category-heading">
          <strong>{normalizedCommandSearch ? "Search results" : selectedCategory.label}</strong>
          <span>
            {normalizedCommandSearch
              ? `${visibleCommandTemplates.length.toLocaleString()} matching command${visibleCommandTemplates.length === 1 ? "" : "s"}`
              : selectedCategory.description}
          </span>
        </div>

        <div className="toolbox-command-list">
          {visibleCommandTemplates.map(({ category, template }) => (
            <article
              key={template.id}
              className="toolbox-command-card"
              draggable
              onDragStart={(event) => onDragStart(event, template)}
              title={`Drag ${template.name} into the script editor`}
            >
              <div className="toolbox-command-drag">
                <GripVertical size={15} />
              </div>
              <button type="button" className="toolbox-command-main" onClick={() => onAddCommand(template)}>
                <strong>
                  {normalizedCommandSearch && <span className={`command-family family-${category.family}`}>{category.label}</span>}
                  {template.name}
                </strong>
                <span>{template.description}</span>
              </button>
            </article>
          ))}
          {!visibleCommandTemplates.length && <p className="muted">No general commands match your search.</p>}
        </div>
      </div>
    </div>
  );
}

function AccordionSection({
  title,
  icon,
  defaultOpen = false,
  lazyMount = false,
  children
}: {
  title: string;
  icon?: React.ReactNode;
  defaultOpen?: boolean;
  lazyMount?: boolean;
  children: React.ReactNode;
}) {
  const [hasMountedChildren, setHasMountedChildren] = useState(defaultOpen || !lazyMount);
  const handleToggle = useCallback((event: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (event.currentTarget.open) setHasMountedChildren(true);
  }, []);

  return (
    <details className="accordion-section" open={defaultOpen} onToggle={lazyMount ? handleToggle : undefined}>
      <summary>
        <span className="accordion-title">
          {icon}
          <span>{title}</span>
        </span>
      </summary>
      <div className="accordion-body">{hasMountedChildren ? children : null}</div>
    </details>
  );
}

function TimelineCommandList({
  commands,
  totalCommandCount,
  activeIndex,
  onSelect
}: {
  commands: CommandModel[];
  totalCommandCount: number;
  activeIndex: number;
  onSelect: (command: CommandModel, index: number) => void;
}) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const [viewport, setViewport] = useState({ scrollTop: 0, height: 0 });

  const updateViewport = useCallback(() => {
    const list = listRef.current;
    if (!list) return;
    setViewport((current) => {
      const nextScrollTop = list.scrollTop;
      const nextHeight = list.clientHeight;
      return current.scrollTop === nextScrollTop && current.height === nextHeight ? current : { scrollTop: nextScrollTop, height: nextHeight };
    });
  }, []);

  useEffect(() => {
    updateViewport();
    const list = listRef.current;
    if (!list || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateViewport);
    observer.observe(list);
    return () => observer.disconnect();
  }, [commands.length, updateViewport]);

  const totalHeight = commands.length * TIMELINE_COMMAND_ROW_HEIGHT;
  const scrollTop = Math.min(viewport.scrollTop, Math.max(0, totalHeight - viewport.height));
  const startIndex = Math.max(0, Math.floor(scrollTop / TIMELINE_COMMAND_ROW_HEIGHT) - TIMELINE_COMMAND_OVERSCAN_ROWS);
  const visibleRowCount = Math.ceil(Math.max(viewport.height, TIMELINE_COMMAND_ROW_HEIGHT) / TIMELINE_COMMAND_ROW_HEIGHT);
  const endIndex = Math.min(commands.length, startIndex + visibleRowCount + TIMELINE_COMMAND_OVERSCAN_ROWS * 2);
  const visibleCommands = commands.slice(startIndex, endIndex);

  return (
    <div className="command-list timeline-command-list" ref={listRef} onScroll={updateViewport}>
      {commands.length > 0 ? (
        <div className="timeline-command-list-spacer" style={{ height: totalHeight }}>
          {visibleCommands.map((command, offset) => (
            <button
              key={command.id || `${command.name}-${command.index}`}
              className={`command-row ${command.index === activeIndex ? "is-active" : ""}`}
              type="button"
              onClick={() => onSelect(command, command.index)}
              title={`${command.operation}${command.targetLabware ? ` -> ${command.targetLabware}` : ""}`}
              style={{ top: (startIndex + offset) * TIMELINE_COMMAND_ROW_HEIGHT }}
            >
              <span className={`command-family family-${command.family}`}>{command.family}</span>
              <span className="command-title">{command.name}</span>
              <span className="command-target">{command.targetLabware || command.group || command.source}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="muted">{totalCommandCount ? "No commands match your search." : "No commands parsed."}</p>
      )}
    </div>
  );
}

function ScriptEditorPanel({
  commands,
  allCommands,
  selectedCommandId,
  draggedCommandId,
  dropTargetCommandId,
  labware,
  validationIssueCount,
  onAdd,
  onSelect,
  onUpdate,
  onDuplicate,
  onDelete,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  onListDragOver,
  onListDrop
}: {
  commands: ScriptEditorCommand[];
  allCommands: ScriptEditorCommand[];
  selectedCommandId: string;
  draggedCommandId: string | null;
  dropTargetCommandId: string | null;
  labware: LabwareModel[];
  validationIssueCount: number;
  onAdd: () => void;
  onSelect: (command: ScriptEditorCommand) => void;
  onUpdate: (commandId: string, patch: ScriptCommandPatch) => void;
  onDuplicate: (commandId: string) => void;
  onDelete: (commandId: string) => void;
  onDragStart: (event: DragEvent<HTMLElement>, commandId: string) => void;
  onDragOver: (event: DragEvent<HTMLElement>, commandId: string) => void;
  onDrop: (event: DragEvent<HTMLElement>, commandId: string) => void;
  onDragEnd: () => void;
  onListDragOver: (event: DragEvent<HTMLElement>) => void;
  onListDrop: (event: DragEvent<HTMLElement>) => void;
}) {
  const enabledCount = allCommands.filter((command) => command.enabled).length;
  return (
    <div className="script-editor-panel">
      <div className={`script-editor-summary ${validationIssueCount ? "has-errors" : ""}`}>
        <div>
          <strong>{enabledCount.toLocaleString()}/{allCommands.length.toLocaleString()}</strong>
          <span>enabled</span>
        </div>
        <div>
          <strong>{validationIssueCount.toLocaleString()}</strong>
          <span>issues</span>
        </div>
        <div className="script-editor-actions">
          <button className="icon-button" type="button" onClick={onAdd} title="Add command">
            <Plus size={16} />
          </button>
        </div>
      </div>

      <div className="script-command-list" aria-label="Editable command queue" onDragOver={onListDragOver} onDrop={onListDrop}>
        {commands.map((command) => {
          const commandIndex = allCommands.findIndex((item) => item.id === command.id);
          const isSelected = command.id === selectedCommandId;
          const isDragging = command.id === draggedCommandId;
          const isDropTarget = Boolean(draggedCommandId && command.id === dropTargetCommandId && command.id !== draggedCommandId);
          return (
            <article
              className={[
                "script-command-card",
                isSelected ? "is-selected" : "",
                isDragging ? "is-dragging" : "",
                isDropTarget ? "is-drop-target" : "",
                command.enabled ? "" : "is-disabled",
                command.validationMessages.length ? "has-errors" : ""
              ].filter(Boolean).join(" ")}
              draggable
              key={command.id}
              onDragStart={(event) => onDragStart(event, command.id)}
              onDragOver={(event) => onDragOver(event, command.id)}
              onDrop={(event) => onDrop(event, command.id)}
              onDragEnd={onDragEnd}
            >
              <div className="script-command-row">
                <button className="script-drag-handle" type="button" title="Drag to reorder" aria-label={`Reorder ${command.name || "command"}`}>
                  <GripVertical size={16} />
                </button>
                <button className="script-command-content" type="button" onClick={() => onSelect(command)} title={scriptCommandTitle(command)}>
                  <span className={`command-family family-${command.family}`}>{command.family}</span>
                  <span className="script-command-copy">
                    <strong>{command.name || "Untitled command"}</strong>
                    <small>{scriptCommandSummary(command)}</small>
                  </span>
                  <span className="script-command-index">{commandIndex >= 0 ? commandIndex + 1 : "-"}</span>
                </button>
                <div className="script-command-actions">
                  <label className="script-command-toggle" title={command.enabled ? "Disable command" : "Enable command"}>
                    <input type="checkbox" checked={command.enabled} onChange={(event) => onUpdate(command.id, { enabled: event.target.checked })} />
                  </label>
                  <button className="icon-button" type="button" onClick={() => onDuplicate(command.id)} title="Duplicate command">
                    <Copy size={15} />
                  </button>
                  <button className="icon-button" type="button" onClick={() => onDelete(command.id)} title="Delete command">
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>

              {isSelected && <ScriptCommandForm command={command} labware={labware} onUpdate={(patch) => onUpdate(command.id, patch)} />}
              {command.validationMessages.length > 0 && (
                <div className="script-command-validation">
                  {command.validationMessages.map((message, messageIndex) => (
                    <span key={`${messageIndex}-${message}`}>{message}</span>
                  ))}
                </div>
              )}
            </article>
          );
        })}
        {!commands.length && <p className="muted">No commands match the current search.</p>}
      </div>
    </div>
  );
}

function ScriptCommandForm({
  command,
  labware,
  onUpdate
}: {
  command: ScriptEditorCommand;
  labware: LabwareModel[];
  onUpdate: (patch: ScriptCommandPatch) => void;
}) {
  const specs = command.specs || {};
  const updateSpec = (key: string, value: string) => onUpdate({ specs: { ...specs, [key]: value } });
  const removeSpec = (key: string) => {
    const nextSpecs = { ...specs };
    delete nextSpecs[key];
    onUpdate({ specs: nextSpecs });
  };

  return (
    <div className="script-command-form">
      <label>
        <span>Name</span>
        <input value={command.name} onChange={(event) => onUpdate({ name: event.target.value })} />
      </label>
      <label>
        <span>Operation</span>
        <input value={command.operation} onChange={(event) => onUpdate({ operation: event.target.value })} />
      </label>
      <label>
        <span>Family</span>
        <select value={command.family} onChange={(event) => onUpdate({ family: event.target.value as OperationFamily })}>
          {OPERATION_FAMILIES.map((family) => (
            <option value={family} key={family}>{family}</option>
          ))}
        </select>
      </label>
      <label>
        <span>Target</span>
        <select value={command.targetLabware} onChange={(event) => onUpdate({ targetLabware: event.target.value })}>
          <option value="">No target</option>
          {labware
            .filter((item) => item.label === command.targetLabware || !isRedundantTemplateLabware(item, labware))
            .map((item) => (
              <option value={item.label} key={item.id || item.label}>{item.label}</option>
            ))}
        </select>
      </label>
      <label>
        <span>Wells</span>
        <CompactWellInput wells={command.wells} onChange={(wells) => onUpdate({ wells })} />
      </label>
      <label>
        <span>Volume uL</span>
        <input
          type="number"
          min="0"
          step="0.1"
          value={command.volumeUl ?? ""}
          onChange={(event) => onUpdate({ volumeUl: numberOrNull(event.target.value) })}
        />
      </label>
      <label className="script-command-form-wide">
        <span>Liquid class</span>
        <input value={command.liquidClass} onChange={(event) => onUpdate({ liquidClass: event.target.value })} />
      </label>
      <label className="script-command-form-wide">
        <span>Message / note</span>
        <textarea
          value={command.message || ""}
          onChange={(event) => onUpdate({ message: event.target.value })}
          placeholder="Prompt text, comment, or operator instruction"
          rows={2}
        />
      </label>
      <div className="script-command-form-wide command-spec-editor">
        <div className="command-spec-heading">
          <span>Command specs</span>
          <button type="button" onClick={() => updateSpec(nextCommandSpecKey(specs), "")}>Add spec</button>
        </div>
        {Object.entries(specs).map(([key, value]) => (
          <div className="command-spec-row" key={key}>
            <input
              value={key}
              aria-label="Spec name"
              onChange={(event) => renameCommandSpec(specs, key, event.target.value, onUpdate)}
              placeholder="name"
            />
            <input value={value} aria-label={`${key} value`} onChange={(event) => updateSpec(key, event.target.value)} placeholder="value" />
            <button type="button" onClick={() => removeSpec(key)} title={`Remove ${key || "spec"}`}>
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        {!Object.keys(specs).length && <p className="muted">No extra specs for this command.</p>}
      </div>
    </div>
  );
}

function CompactWellInput({ wells, onChange }: { wells: string[]; onChange: (wells: string[]) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(formatWellList(wells));
  const fullValue = formatWellList(wells);
  const summaryValue = wells.length ? compactWellList(wells) : "";

  useEffect(() => {
    if (!editing) setDraft(fullValue);
  }, [editing, fullValue]);

  function handleFocus() {
    setEditing(true);
    setDraft(fullValue);
  }

  function handleBlur(event: ReactFocusEvent<HTMLInputElement>) {
    const nextWells = parseWellList(event.target.value);
    onChange(nextWells);
    setDraft(formatWellList(nextWells));
    setEditing(false);
  }

  return (
    <input
      className={wells.length > 8 && !editing ? "is-compact-well-list" : ""}
      value={editing ? draft : summaryValue}
      onFocus={handleFocus}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={handleBlur}
      placeholder="A1, A2, B1"
      title={fullValue || "No wells specified"}
    />
  );
}

function ObjectLibraryPanel({
  items,
  activeItemId,
  enabled,
  onDragStart,
  onDragEnd
}: {
  items: ObjectLibraryItem[];
  activeItemId: string;
  enabled: boolean;
  onDragStart: (event: DragEvent<HTMLElement>, item: ObjectLibraryItem) => void;
  onDragEnd: () => void;
}) {
  return (
    <div className="object-library-list" aria-label="Object library">
      {items.map((item) => (
        <article
          className={`object-library-card ${item.id === activeItemId ? "is-dragging" : ""} ${enabled ? "" : "is-disabled"}`}
          key={item.id}
          draggable={enabled}
          onDragStart={(event) => onDragStart(event, item)}
          onDragEnd={onDragEnd}
          title={enabled ? `Drag ${item.name} onto the 3D deck` : "Switch to Edit mode to place objects"}
          aria-disabled={!enabled}
        >
          <div className="object-library-swatch" style={{ backgroundColor: item.color }} />
          <div className="object-library-copy">
            <strong>{item.name}</strong>
            <span>{objectLibrarySummary(item)}</span>
            <small>{item.tags.slice(0, 4).join(" · ")}</small>
          </div>
        </article>
      ))}
      {!items.length && <p className="muted">No objects match your search.</p>}
    </div>
  );
}

function ProtocolOverviewPanel({ stats, warnings, hiddenWarningCount }: { stats: ProtocolStats; warnings: string[]; hiddenWarningCount: number }) {
  return (
    <>
      <div className="metric-grid">
        <Metric label="Labware" value={stats.labware} />
        <Metric label="Commands" value={stats.commands} />
        <Metric label="Liquid" value={stats.liquidCommands} />
        <Metric label="Tips" value={stats.tipCommands} />
      </div>
      {warnings.map((warning, index) => (
        <div className="notice-row" key={`model-warning-${index}`}>
          <AlertTriangle size={16} />
          <span>{warning}</span>
        </div>
      ))}
      {hiddenWarningCount > 0 && <p className="muted">+ {hiddenWarningCount} more warning{hiddenWarningCount === 1 ? "" : "s"}</p>}
    </>
  );
}

function SourceSummariesPanel({ summaries, hiddenCount }: { summaries: ProtocolModel["sourceSummaries"]; hiddenCount: number }) {
  return (
    <div className="artifact-list">
      {summaries.map((artifact, index) => (
        <div
          className="artifact-row"
          key={`${index}-${artifact.name}-${artifact.kind}`}
          title={`${artifact.count.toLocaleString()} parsed item${artifact.count === 1 ? "" : "s"}`}
        >
          <span className={`kind-pill kind-${artifact.kind.replace(/[^a-z]/g, "")}`}>{artifact.kind}</span>
          <span>{artifact.name}</span>
        </div>
      ))}
      {hiddenCount > 0 && <p className="muted">+ {hiddenCount.toLocaleString()} more artifact summaries</p>}
      {!summaries.length && <p className="muted">No files loaded</p>}
    </div>
  );
}

function ZeiaContextPanel({
  model,
  imageOverrides,
  onPreviewAsset,
  onExportImageBundle
}: {
  model: ProtocolModel;
  imageOverrides: Record<string, string>;
  onPreviewAsset: (assetId: string) => void;
  onExportImageBundle: () => void;
}) {
  const overrideCount = Object.keys(imageOverrides).length;
  return (
    <>
      <div className="hardware-grid">
        <span>Assets</span>
        <strong>{model.hardware.packagedAssets}/{model.hardware.assets}</strong>
        <span>Connectors</span>
        <strong>{model.hardware.connectors}</strong>
        <span>Pins</span>
        <strong>{model.hardware.pins}</strong>
      </div>
      {model.hardware.missingAssets.length > 0 && (
        <div className="notice-row">
          <AlertTriangle size={16} />
          <span>{model.hardware.missingAssets.length} asset reference{model.hardware.missingAssets.length === 1 ? "" : "s"} need manual verification</span>
        </div>
      )}
      {model.hardware.notes.slice(0, MAX_HARDWARE_NOTES).map((note, index) => (
        <p className="muted" key={`hardware-note-${index}`}>{note}</p>
      ))}
      <div className="image-override-actions">
        <span>{overrideCount.toLocaleString()} replacement image{overrideCount === 1 ? "" : "s"}</span>
        <button className="icon-button with-label" type="button" onClick={onExportImageBundle} disabled={!overrideCount} title="Export replacement images as a generated asset bundle">
          <Download size={15} />
          <span>Bundle</span>
        </button>
      </div>
      <HardwareAssetGallery assets={model.hardware.assetDetails} imageOverrides={imageOverrides} onPreviewAsset={onPreviewAsset} />
    </>
  );
}

function MeshInventoryPanel({ inventory, coverage }: { inventory: MeshInventorySummary; coverage: GeometryCoverageSummary }) {
  const hasMeshes = inventory.items.length > 0;
  const isChecking = inventory.status === "checking";
  const visibleMissing = inventory.missingItems.slice(0, MAX_MISSING_MESH_ROWS);
  const hiddenMissingCount = Math.max(0, inventory.missingItems.length - visibleMissing.length);
  const visibleFallbackTargets = coverage.fallbackCommandedTargets.slice(0, 3);
  const hiddenFallbackTargets = Math.max(0, coverage.fallbackCommandedTargets.length - visibleFallbackTargets.length);

  return (
    <div className="mesh-inventory-panel">
      <div className="mesh-inventory-grid">
        <span>Worktable meshes found</span>
        <strong>{inventory.items.length.toLocaleString()}</strong>
        <span>Converted GLBs found</span>
        <strong>{isChecking ? "..." : inventory.convertedCount.toLocaleString()}</strong>
        <span>Using primitive fallback</span>
        <strong>{isChecking ? "..." : inventory.fallbackCount.toLocaleString()}</strong>
        <span>Exact commanded well targets</span>
        <strong>
          {coverage.exactCommandedWellTargets.toLocaleString()}/{coverage.commandedWellTargets.toLocaleString()}
        </strong>
        <span>Exact transformed well labware</span>
        <strong>
          {coverage.exactTransformedWellLabware.toLocaleString()}/{coverage.transformedWellLabware.toLocaleString()}
        </strong>
        <span>Anchored protocol locations</span>
        <strong>{coverage.anchoredProtocolLocations.toLocaleString()}</strong>
      </div>

      {isChecking && hasMeshes && <p className="muted mesh-inventory-note">Checking converted model assets...</p>}
      {!hasMeshes && <p className="muted mesh-inventory-note">No Worktable/Meshes/*.xmsh entries loaded.</p>}
      {inventory.status === "complete" && inventory.error && (
        <div className="notice-row">
          <AlertTriangle size={16} />
          <span>{inventory.error}</span>
        </div>
      )}

      {inventory.status === "complete" && hasMeshes && !inventory.missingItems.length && (
        <p className="mesh-ok">All loaded worktable meshes have converted GLBs.</p>
      )}

      {visibleFallbackTargets.length > 0 && (
        <p className="muted mesh-inventory-note">
          Fallback well targets: {visibleFallbackTargets.join(", ")}
          {hiddenFallbackTargets ? ` + ${hiddenFallbackTargets.toLocaleString()} more` : ""}
        </p>
      )}

      {visibleMissing.length > 0 && (
        <div className="missing-mesh-list" aria-label="Missing converted models">
          <span>Missing converted models</span>
          {visibleMissing.map((mesh) => (
            <div className="missing-mesh-row" key={`${mesh.guid || mesh.name}-${mesh.sourcePath}`} title={mesh.assetPath}>
              <strong>{mesh.name || mesh.guid || "Unnamed mesh"}</strong>
              <small>{mesh.guid ? mesh.guid.slice(0, 8) : pathBasename(mesh.sourcePath)}</small>
            </div>
          ))}
          {hiddenMissingCount > 0 && <p className="muted">+ {hiddenMissingCount.toLocaleString()} more missing model{hiddenMissingCount === 1 ? "" : "s"}</p>}
        </div>
      )}
    </div>
  );
}

function HardwareAssetGallery({ assets, imageOverrides, onPreviewAsset }: { assets: HardwareAssetModel[]; imageOverrides: Record<string, string>; onPreviewAsset: (assetId: string) => void }) {
  const visibleAssets = assets.slice(0, MAX_HARDWARE_ASSETS);
  const hiddenCount = Math.max(0, assets.length - visibleAssets.length);
  if (!visibleAssets.length) return <p className="muted asset-empty">No hardware asset details loaded.</p>;

  return (
    <>
      <div className="asset-gallery">
        {visibleAssets.map((asset) => {
          const imageSrc = imageOverrides[asset.id] || asset.imageSrc || "";
          return (
            <button
              className={`asset-card ${asset.manualVerificationRequired ? "needs-review" : ""}`}
              key={asset.id}
              title={asset.bundlePath || asset.sourcePath || asset.entry}
              type="button"
              onClick={() => onPreviewAsset(asset.id)}
            >
              <div className="asset-thumb">
                {imageSrc ? <img src={imageSrc} alt={asset.name} loading="lazy" /> : <ImageIcon size={20} />}
              </div>
              <div className="asset-copy">
                <strong>{asset.name}</strong>
                <span>{asset.profile.replace(/-/g, " ")}</span>
                <small>{asset.matchedLabware.slice(0, 2).join(", ") || asset.status.replace(/_/g, " ")}</small>
              </div>
            </button>
          );
        })}
      </div>
      {hiddenCount > 0 && <p className="muted asset-empty">+ {hiddenCount.toLocaleString()} more hardware asset{hiddenCount === 1 ? "" : "s"}</p>}
    </>
  );
}

function HardwareImageModal({
  asset,
  imageSrc,
  onChangeImage,
  onClose
}: {
  asset: HardwareAssetModel;
  imageSrc: string;
  onChangeImage: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="image-modal" role="dialog" aria-modal="true" aria-label={`${asset.name} preview`} onClick={(event) => event.stopPropagation()}>
        <div className="image-modal-header">
          <div>
            <strong>{asset.name}</strong>
            <span>{asset.profile.replace(/-/g, " ")}</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose} title="Close image preview" aria-label="Close image preview">
            ×
          </button>
        </div>
        <div className="image-modal-preview">
          {imageSrc ? <img src={imageSrc} alt={asset.name} /> : <ImageIcon size={56} />}
        </div>
        <button className="icon-button with-label change-image-button" type="button" onClick={onChangeImage}>
          <Upload size={18} />
          <span>Change image</span>
        </button>
      </div>
    </div>
  );
}

function CommandDetails({ command, activeIndex, total }: { command: CommandModel; activeIndex: number; total: number }) {
  const coreRows: CommandDetailRow[] = [
    { label: "Operation", value: command.operation },
    { label: "Source", value: command.source },
    { label: "Target", value: command.targetLabware || "none" }
  ];
  if (command.labwareTransfer) coreRows.push({ label: "Destination", value: formatTransferDestination(command.labwareTransfer) });

  const optionalRows: CommandDetailRow[] = [
    { label: "Wells", value: command.labwareTransfer ? "not applicable" : formatWells(command.wells) },
    { label: "Volume", value: command.labwareTransfer ? "not applicable" : formatVolume(command.volumeUl) },
    { label: "Liquid class", value: command.labwareTransfer ? "not applicable" : command.liquidClass || "none" },
    { label: "Head", value: command.labwareTransfer ? "not applicable" : formatHeadKind(command) },
    { label: "Channels", value: command.labwareTransfer ? "not applicable" : command.channelCount === null ? "unknown" : command.channelCount.toLocaleString() },
    { label: "Active tips", value: command.labwareTransfer ? "not applicable" : formatActiveTips(command) },
    { label: "Tip spacing", value: command.labwareTransfer ? "not applicable" : formatTipSpacing(command) }
  ];

  const visibleOptionalRows = optionalRows.filter((row) => !shouldCollapseCommandDetail(row.value));
  const collapsedOptionalRows = optionalRows.filter((row) => shouldCollapseCommandDetail(row.value));

  return (
    <div className="command-details">
      <div className="command-index">
        <span>{activeIndex + 1}</span>
        <span>/</span>
        <span>{total}</span>
      </div>
      <h3>{command.name}</h3>
      <dl>
        {[...coreRows, ...visibleOptionalRows].map((row) => (
          <FragmentRow key={row.label} row={row} />
        ))}
      </dl>
      {collapsedOptionalRows.length > 0 && (
        <details className="low-signal-command-details">
          <summary>Unknown / not applicable details ({collapsedOptionalRows.length})</summary>
          <dl>
            {collapsedOptionalRows.map((row) => (
              <FragmentRow key={row.label} row={row} />
            ))}
          </dl>
        </details>
      )}
      {command.message && <p className="command-message">{command.message}</p>}
    </div>
  );
}

function FragmentRow({ row }: { row: CommandDetailRow }) {
  return (
    <>
      <dt>{row.label}</dt>
      <dd>{row.value}</dd>
    </>
  );
}

type GateTargetLink = {
  id: string;
  label: string;
  kind: SceneFocusTarget["kind"];
  commandIndex: number | null;
  focusTarget: SceneFocusTarget;
};

function ValidationGatesPanel({
  model,
  onSelectTarget
}: {
  model: ProtocolModel;
  onSelectTarget: (commandIndex: number | null, focusTarget: SceneFocusTarget) => void;
}) {
  const gates = model.repairs.validationGates;
  const failed = gates.filter((gate) => gate.severity === "blocking").length;
  const review = gates.filter((gate) => gate.severity === "needs_review").length;
  const passed = gates.filter((gate) => gate.status === "passed").length;

  return (
    <div className="validation-panel">
      <div className={`gate-summary ${failed ? "severity-blocking" : review ? "severity-needs_review" : passed ? "severity-safe" : "severity-info"}`}>
        <span>{gates.length ? `${passed}/${gates.length}` : "0/0"}</span>
        <strong>{failed}</strong>
        <span>failed</span>
        <strong>{review}</strong>
        <span>review</span>
      </div>

      <div className="gate-list">
        {gates.map((gate) => (
          <ValidationGateRow key={gate.id} gate={gate} links={gateTargetLinks(gate, model)} onSelectTarget={onSelectTarget} />
        ))}
        {!gates.length && <p className="muted">No ready validation gates loaded.</p>}
      </div>
    </div>
  );
}

function ReadinessPanel({ model }: { model: ProtocolModel }) {
  const readiness = model.readiness;
  const items: Array<[string, ReadinessState]> = readiness
    ? [
        ["Offline Validation", readiness.offline_validation],
        ["Review State", readiness.review_state],
        ["FluentControl Load Diagnostic", readiness.fluentcontrol_load_diagnostic],
        ["Generated ZEIA Import", readiness.generated_zeia_import],
        ["Script Editor Load", readiness.script_editor_load],
        ["Simulation", readiness.simulation],
        ["Hardware Run", readiness.hardware_run]
      ]
    : [];

  return (
    <div className="readiness-panel">
      <div className="readiness-summary">
        <span>Overall</span>
        <strong>{model.readinessStatus || "unknown"}</strong>
      </div>
      {items.length ? (
        <div className="readiness-list">
          {items.map(([label, state]) => (
              <div key={label} className="readiness-row">
              <div>
                <strong>{label}</strong>
                <span>{String(state.status || "unknown").replace(/_/g, " ")}</span>
              </div>
              <small>{typeof state.summary === "string" && state.summary.trim() ? state.summary : "No summary provided."}</small>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">No canonical readiness object loaded.</p>
      )}
    </div>
  );
}

function ValidationGateRow({
  gate,
  links,
  onSelectTarget
}: {
  gate: ValidationGate;
  links: GateTargetLink[];
  onSelectTarget: (commandIndex: number | null, focusTarget: SceneFocusTarget) => void;
}) {
  const isOpen = gate.status !== "passed" && gate.status !== "not_configured";
  return (
    <details className={`gate-row severity-${gate.severity}`} open={isOpen}>
      <summary>
        <span className="gate-number">{gate.gate}</span>
        <span className="gate-title">{gate.name}</span>
        <span className="gate-status">{gate.status.replace("_", " ")}</span>
      </summary>
      <div className="gate-body">
        <p>{gate.summary || "No summary was provided."}</p>
        <div className="gate-links">
          {links.map((link) => (
            <button key={link.id} type="button" onClick={() => onSelectTarget(link.commandIndex, link.focusTarget)} title={`Focus ${link.label}`}>
              <LocateFixed size={13} />
              <span>{link.label}</span>
            </button>
          ))}
          {!links.length && (
            <span className="gate-link-empty">
              <CheckCircle2 size={13} />
              No linked deck target
            </span>
          )}
        </div>
      </div>
    </details>
  );
}

function WorktableDiffPanel({
  repairs,
  model,
  onSelectTarget
}: {
  repairs: WorktableRepairModel;
  model: ProtocolModel;
  onSelectTarget: (commandIndex: number | null, focusTarget: SceneFocusTarget) => void;
}) {
  const issueGroups: Array<{ title: string; kinds: WorktableIssue["kind"][] }> = [
    { title: "Missing Labware", kinds: ["missing_labware", "tip_box", "carrier"] },
    { title: "Changed Positions", kinds: ["changed_position", "worktable"] },
    { title: "Liquid Classes", kinds: ["liquid_class"] },
    { title: "Device Aliases", kinds: ["device_alias"] },
    { title: "Worklists", kinds: ["worklist"] },
    { title: "Validation", kinds: ["validation", "warning"] }
  ];

  return (
    <div className="diff-panel">
      <div className={`diff-summary severity-${repairs.summary.overallSeverity}`}>
        <span>{repairs.summary.overallSeverity.replace("_", " ")}</span>
        <strong>{repairs.summary.blocking}</strong>
        <span>blocking</span>
        <strong>{repairs.summary.needsReview}</strong>
        <span>review</span>
      </div>

      {(repairs.sourceWorktable || repairs.protocolWorktable) && (
        <div className="worktable-pair">
          <span>Source</span>
          <strong>{repairs.sourceWorktable || "unknown"}</strong>
          <span>Protocol</span>
          <strong>{repairs.protocolWorktable || "unknown"}</strong>
        </div>
      )}

      <div className="diff-groups">
        {issueGroups.map((group) => (
          <IssueGroup
            key={group.title}
            title={group.title}
            issues={repairs.issues.filter((issue) => group.kinds.includes(issue.kind))}
            model={model}
            onSelectTarget={onSelectTarget}
          />
        ))}
      </div>

      <div className="section-heading repair-heading">
        <Wrench size={16} />
        <h2>Alias Repairs</h2>
      </div>
      <div className="alias-repair-list">
        {repairs.aliasRepairs.slice(0, MAX_ALIAS_REPAIRS).map((repair) => (
          <div className="alias-repair-row" key={repair.id}>
            <span className={`repair-status status-${repair.status.replace(/[^a-z]/g, "")}`}>{repair.status}</span>
            <div>
              <strong>{repair.oldName || repair.kind}</strong>
              {repair.newName && <span> {"->"} {repair.newName}</span>}
              <p>{repair.summary}</p>
            </div>
          </div>
        ))}
        {!repairs.aliasRepairs.length && <p className="muted">No alias repairs reported.</p>}
      </div>
    </div>
  );
}

function IssueGroup({
  title,
  issues,
  model,
  onSelectTarget
}: {
  title: string;
  issues: WorktableIssue[];
  model: ProtocolModel;
  onSelectTarget: (commandIndex: number | null, focusTarget: SceneFocusTarget) => void;
}) {
  return (
    <details className="issue-group" open={issues.some((issue) => issue.severity !== "safe")}>
      <summary>
        <span>{title}</span>
        <strong>{issues.length}</strong>
      </summary>
      <div className="issue-list">
        {issues.map((issue) => {
          const link = issueTargetLink(issue, model);
          const body = (
            <>
              <span className="issue-status">{issue.status}</span>
              <div>
                <strong>{issue.label}</strong>
                <p>{issue.detail || issue.suggestion}</p>
                {(issue.sourceValue || issue.targetValue) && (
                  <small>
                    {issue.sourceValue || "source unknown"} {"->"} {issue.targetValue || "target unknown"}
                  </small>
                )}
                {link && (
                  <span className="issue-focus-hint">
                    <LocateFixed size={12} /> Focus {link.label}
                  </span>
                )}
              </div>
            </>
          );
          if (link) {
            return (
              <button
                className={`issue-row issue-row-button severity-${issue.severity}`}
                key={issue.id}
                type="button"
                onClick={() => onSelectTarget(link.commandIndex, link.focusTarget)}
                title={`Focus ${link.label}`}
              >
                {body}
              </button>
            );
          }
          return (
            <div className={`issue-row severity-${issue.severity}`} key={issue.id}>
              {body}
            </div>
          );
        })}
        {!issues.length && <p className="muted">No items.</p>}
      </div>
    </details>
  );
}

function DevConsole({ logs, onClose, onCopy }: { logs: DevLogEntry[]; onClose: () => void; onCopy: () => void }) {
  return (
    <div className="dev-console" role="dialog" aria-modal="false" aria-label="Developer tools">
      <div className="dev-console-header">
        <div>
          <strong>Dev mode</strong>
          <span>Press ` or ~ to toggle</span>
        </div>
        <div className="dev-console-actions">
          <button className="icon-button with-label" type="button" onClick={onCopy}>
            <span>Copy logs</span>
          </button>
          <button className="icon-button" type="button" onClick={onClose} title="Close dev mode">
            ×
          </button>
        </div>
      </div>
      <textarea
        className="dev-log-output"
        readOnly
        value={logs.length ? logs.map(formatDevLogEntry).join("\n") : "No dev logs captured yet."}
      />
    </div>
  );
}

function gateTargetLinks(gate: ValidationGate, model: ProtocolModel): GateTargetLink[] {
  if (gate.status === "passed" || gate.status === "not_configured" || gate.status === "not_available") return [];

  const links: GateTargetLink[] = [];
  const haystack = gateText(gate);
  const stepIndexes = detailNumbersByKey(gate.details, /(step|command).*index/i);
  stepIndexes.forEach((index) => {
    const command = commandForIndex(model.commands, index);
    if (command) links.push(commandLink(command, command.index, "Command"));
  });

  model.commands.forEach((command) => {
    const tokens = [command.id, command.commandId, command.name, command.operation].filter((token) => token.length > 3);
    if (tokens.some((token) => containsToken(haystack, token))) {
      links.push(commandLink(command, command.index, command.family === "liquid" ? "Liquid move" : "Command"));
    }
  });

  const matchedLabware = model.labware.filter((labware) => labwareSearchTokens(labware).some((token) => containsToken(haystack, token)));
  matchedLabware.forEach((labware) => {
    const commandIndex = firstCommandIndexForLabware(model.commands, labware.label);
    links.push({
      id: `labware-${labware.id}`,
      label: `Labware: ${labware.label}`,
      kind: "labware",
      commandIndex,
      focusTarget: {
        kind: "labware",
        label: labware.label,
        labwareLabel: labware.label,
        slotIndex: labware.position ? labware.position - 1 : labware.slotIndex
      }
    });
  });

  detailNumbersByKey(gate.details, /(slot|site|position|deck_location)/i).forEach((slotNumber) => {
    if (!Number.isFinite(slotNumber) || slotNumber <= 0) return;
    links.push({
      id: `site-${slotNumber}`,
      label: `Site ${slotNumber}`,
      kind: "site",
      commandIndex: null,
      focusTarget: {
        kind: "site",
        label: `Site ${slotNumber}`,
        slotIndex: slotNumber - 1
      }
    });
  });

  if (/liquid|volume|tip|well/i.test(`${gate.id} ${gate.name}`) && !links.some((link) => link.kind === "liquid")) {
    const liquidCommand = model.commands.find((command) => command.family === "liquid") || model.commands.find((command) => command.family === "tips");
    if (liquidCommand) links.push(commandLink(liquidCommand, liquidCommand.index, liquidCommand.family === "liquid" ? "Liquid move" : "Tip command"));
  }

  return dedupeLinks(links).slice(0, 5);
}

function commandLink(command: CommandModel, commandIndex: number, labelPrefix: string): GateTargetLink {
  return {
    id: `command-${command.id || command.index}`,
    label: `${labelPrefix}: ${command.name}`,
    kind: command.family === "liquid" ? "liquid" : "command",
    commandIndex,
    focusTarget: commandFocusTarget(command)
  };
}

function commandFocusTarget(command: CommandModel): SceneFocusTarget {
  return {
    kind: command.family === "liquid" ? "liquid" : "command",
    label: command.name,
    labwareLabel: command.targetLabware || undefined,
    commandId: command.id
  };
}

function sceneFocusTargetsMatch(left: SceneFocusTarget | null, right: SceneFocusTarget | null): boolean {
  if (!left || !right) return left === right;
  return (
    left.kind === right.kind &&
    left.label === right.label &&
    left.labwareLabel === right.labwareLabel &&
    left.commandId === right.commandId &&
    left.slotIndex === right.slotIndex
  );
}

function commandForIndex(commands: CommandModel[], index: number): CommandModel | undefined {
  if (!Number.isFinite(index)) return undefined;
  return commands.find((command) => command.index === index) || commands.find((command) => command.index === index - 1) || commands[index] || commands[index - 1];
}

function firstCommandIndexForLabware(commands: CommandModel[], labwareLabel: string): number | null {
  const command = commands.find((item) => labwareLabelMatches(item.targetLabware, labwareLabel));
  return command ? command.index : null;
}

function issueTargetLink(issue: WorktableIssue, model: ProtocolModel): GateTargetLink | null {
  const haystack = normalizeSearchText(
    `${issue.label} ${issue.detail} ${issue.suggestion} ${issue.sourceValue} ${issue.targetValue} ${issue.diffPath}`
  );
  const matched =
    model.labware.find((labware) => labwareLabelMatches(labware.label, issue.label)) ||
    model.labware.find((labware) => labwareSearchTokens(labware).some((token) => containsToken(haystack, token)));
  if (!matched) return null;
  return {
    id: `issue-${issue.id}`,
    label: `Labware: ${matched.label}`,
    kind: "labware",
    commandIndex: firstCommandIndexForLabware(model.commands, matched.label),
    focusTarget: {
      kind: "labware",
      label: matched.label,
      labwareLabel: matched.label,
      slotIndex: matched.position ? matched.position - 1 : matched.slotIndex
    }
  };
}

const SEVERITY_RANK: Record<DiffSeverity, number> = { blocking: 3, needs_review: 2, safe: 1, info: 0 };

function validationHighlightsFor(model: ProtocolModel): ValidationHighlight[] {
  const highlights = new Map<string, ValidationHighlight>();

  const register = (labwareLabel: string, slotIndex: number | undefined, severity: DiffSeverity, reason: string) => {
    if (!labwareLabel || (severity !== "blocking" && severity !== "needs_review")) return;
    const key = normalizeSearchText(labwareLabel);
    if (!key) return;
    const existing = highlights.get(key);
    if (existing) {
      if (SEVERITY_RANK[severity] > SEVERITY_RANK[existing.severity]) existing.severity = severity;
      if (reason && !existing.reasons.includes(reason)) existing.reasons.push(reason);
      if (existing.slotIndex === undefined && slotIndex !== undefined) existing.slotIndex = slotIndex;
      return;
    }
    highlights.set(key, { labwareLabel, slotIndex, severity, reasons: reason ? [reason] : [] });
  };

  model.repairs.validationGates.forEach((gate) => {
    if (gate.severity !== "blocking" && gate.severity !== "needs_review") return;
    gateTargetLinks(gate, model)
      .filter((link) => link.kind === "labware" && Boolean(link.focusTarget.labwareLabel))
      .forEach((link) =>
        register(link.focusTarget.labwareLabel as string, link.focusTarget.slotIndex, gate.severity, `Gate ${gate.gate}: ${gate.name}`)
      );
  });

  model.repairs.issues.forEach((issue) => {
    if (issue.severity !== "blocking" && issue.severity !== "needs_review") return;
    const link = issueTargetLink(issue, model);
    if (!link || !link.focusTarget.labwareLabel) return;
    register(link.focusTarget.labwareLabel, link.focusTarget.slotIndex, issue.severity, issue.label || issue.kind);
  });

  return Array.from(highlights.values());
}

function labwareSearchTokens(labware: ProtocolModel["labware"][number]): string[] {
  return uniqueStrings([
    labware.label,
    labware.catalogName,
    labware.geometryName,
    labware.location,
    labware.source,
    ...labware.hardwareAssetNames,
    ...labware.hardwareAssetIds
  ]).filter((token) => token.length > 3);
}

function gateText(gate: ValidationGate): string {
  return normalizeSearchText(`${gate.id} ${gate.gate} ${gate.name} ${gate.summary} ${JSON.stringify(gate.details)}`);
}

function containsToken(haystack: string, token: string): boolean {
  const normalizedToken = normalizeSearchText(token);
  return normalizedToken.length > 3 && haystack.includes(normalizedToken);
}

function detailNumbersByKey(value: unknown, keyPattern: RegExp): number[] {
  const matches: number[] = [];
  visitDetails(value, (key, item) => {
    if (!keyPattern.test(key) || /count/i.test(key)) return;
    const parsed = typeof item === "number" ? item : typeof item === "string" ? Number(item) : Number.NaN;
    if (Number.isFinite(parsed)) matches.push(parsed);
  });
  return Array.from(new Set(matches));
}

function visitDetails(value: unknown, visitor: (key: string, value: unknown) => void): void {
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach((item) => visitDetails(item, visitor));
    return;
  }
  Object.entries(value as Record<string, unknown>).forEach(([key, item]) => {
    visitor(key, item);
    visitDetails(item, visitor);
  });
}

function dedupeLinks(links: GateTargetLink[]): GateTargetLink[] {
  return Array.from(
    new Map(
      links.map((link) => [
        `${link.kind}:${link.commandIndex ?? ""}:${normalizeSearchText(link.focusTarget.labwareLabel || "")}:${link.focusTarget.slotIndex ?? ""}:${link.focusTarget.commandId ?? ""}`,
        link
      ])
    ).values()
  );
}

function embeddedProtocolModelFromArtifacts(artifacts: SourceArtifact[]): ProtocolModel | null {
  // A re-imported simulator save embeds the full parsed protocol. The save file
  // doesn't re-ship the original source artifacts (XSCR/GWL/mesh), so re-parsing
  // the lone JSON would lose the worktable. Use the embedded protocol directly so
  // a project round-trips (export/save -> import) without fidelity loss.
  const payload = simulatorProjectPayloadFromArtifacts(artifacts);
  if (!payload || !isRecord(payload.protocol)) return null;
  const protocol = payload.protocol;
  if (!Array.isArray(protocol.labware) || !Array.isArray(protocol.commands)) return null;
  return { ...EMPTY_MODEL, ...(protocol as unknown as ProtocolModel) };
}

function safeBuildProtocolModel(artifacts: SourceArtifact[]): ProtocolModel {
  if (!artifacts.length) return EMPTY_MODEL;
  try {
    const embedded = embeddedProtocolModelFromArtifacts(artifacts);
    if (embedded) return embedded;
    return buildProtocolModel(artifacts);
  } catch (error) {
    const message = errorMessageFor(error);
    return {
      ...EMPTY_MODEL,
      id: `build-error-${artifacts.length}-${hashText(artifacts.map((artifact) => artifact.id).join("|"))}`,
      name: "Protocol Parse Failed",
      context: "local workspace",
      sourceSummaries: artifacts.map((artifact) => ({ name: artifact.name, kind: artifact.kind, count: 1 })),
      warnings: [`Could not build protocol model: ${message}`]
    };
  }
}

function buildSavedScriptArtifact(
  artifacts: SourceArtifact[],
  model: ProtocolModel,
  selectedSampleId: string,
  imageOverrides: Record<string, string>,
  scriptCommands: ScriptEditorCommand[],
  placementOverrides: ScenePlacementOverride[],
  placedObjects: LabwareModel[],
  uiState: Record<string, unknown>
): SavedScriptArtifact {
  const title = sanitizeScriptTitle(model.name || "protocol") || "protocol";
  const slug = modelAssetSlug(title);
  const protocolIr = artifacts.find((artifact) => artifact.kind === "protocol-ir" && artifact.text.trim());
  if (protocolIr) {
    try {
      const payload = JSON.parse(protocolIr.text) as Record<string, unknown>;
      const protocol = isRecord(payload.protocol) ? payload.protocol : {};
      payload.protocol = { ...protocol, name: title };
      return {
        fileName: `${slug}.protocol-ir.json`,
        body: JSON.stringify(payload, null, 2) + "\n",
        mimeType: "application/json"
      };
    } catch {
      return {
        fileName: `${slug}.protocol-ir.json`,
        body: JSON.stringify(savedSimulatorPayload(artifacts, model, selectedSampleId, imageOverrides, scriptCommands, placementOverrides, placedObjects, uiState), null, 2) + "\n",
        mimeType: "application/json"
      };
    }
  }

  const xscr = artifacts.find((artifact) => artifact.kind === "xscr" && artifact.text.trim());
  if (xscr) {
    return {
      fileName: `${slug}.xscr`,
      body: renameXscrTitle(xscr.text, title),
      mimeType: "application/xml"
    };
  }

  return {
    fileName: `${slug}-simulator-save.json`,
    body: JSON.stringify(savedSimulatorPayload(artifacts, model, selectedSampleId, imageOverrides, scriptCommands, placementOverrides, placedObjects, uiState), null, 2) + "\n",
    mimeType: "application/json"
  };
}

function savedSimulatorPayload(
  artifacts: SourceArtifact[],
  model: ProtocolModel,
  selectedSampleId: string,
  imageOverrides: Record<string, string>,
  scriptCommands: ScriptEditorCommand[],
  placementOverrides: ScenePlacementOverride[],
  placedObjects: LabwareModel[],
  uiState: Record<string, unknown>
): Record<string, unknown> {
  return {
    savedAt: new Date().toISOString(),
    app: "tecan-protocol-simulator",
    kind: "simulator-project",
    schemaVersion: 2,
    sampleId: selectedSampleId || null,
    scriptTitle: model.name,
    protocol: model,
    editor: {
      version: 1,
      scriptCommands,
      addedObjects: addedObjectsForExport(model, placedObjects),
      imageOverrides: imageOverridesForExport(imageOverrides, model.hardware.assetDetails),
      placementOverrides,
      ui: {
        ...uiState,
        scriptTitle: model.name
      },
      notes: []
    },
    assetImageOverrides: imageOverridesForExport(imageOverrides, model.hardware.assetDetails),
    artifacts: artifacts.map((artifact) => ({
      id: artifact.id,
      name: artifact.name,
      kind: artifact.kind,
      source: artifact.source,
      path: artifact.path,
      size: artifact.size,
      text: artifact.kind === "hardware-image" ? "" : artifact.text
    }))
  };
}

function renameXscrTitle(text: string, title: string): string {
  const replacement = `$1${escapeXmlText(title)}$2`;
  if (/<ObjectName(?:\s[^>]*)?>[\s\S]*?<\/ObjectName>/i.test(text)) {
    return text.replace(/(<ObjectName(?:\s[^>]*)?>)[\s\S]*?(<\/ObjectName>)/i, replacement);
  }
  return `<!-- Simulator script title: ${escapeXmlText(title)} -->\n${text}`;
}

function downloadText(fileName: string, body: string, mimeType: string) {
  const blob = new Blob([body], { type: mimeType });
  downloadBlob(fileName, blob, mimeType);
}

function downloadBlob(fileName: string, blob: Blob, mimeType: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function validateScriptEditorCommand(command: ScriptEditorCommand, labware: LabwareModel[]): ScriptEditorCommand {
  const family = command.family || familyForEditorOperation(command.operation);
  const messages: string[] = [];
  const name = command.name.trim();
  const operation = command.operation.trim();
  const targetLabware = command.targetLabware.trim();
  const target = targetLabware ? labware.find((item) => labwareLabelMatches(item.label, targetLabware)) : null;
  const headAdapterCommand = isHeadAdapterEditorCommand(operation);

  if (!name) messages.push("Command name is empty.");
  if (!operation) messages.push("Operation is empty.");
  if (targetLabware && !target && !headAdapterCommand) messages.push(`Target labware "${targetLabware}" was not found.`);
  if (family === "liquid") {
    if (!targetLabware) messages.push("Liquid command needs target labware.");
    if (command.volumeUl === null || command.volumeUl <= 0) messages.push("Liquid command needs a volume greater than 0 uL.");
    if (!command.liquidClass.trim()) messages.push("Liquid command needs a liquid class.");
    if (target && command.wells.length) {
      const targetWells = new Set(target.wells.map((well) => normalizeSearchText(well.id)));
      const missingWells = command.wells.filter((well) => !targetWells.has(normalizeSearchText(well)));
      if (missingWells.length) messages.push(`Unknown well${missingWells.length === 1 ? "" : "s"}: ${missingWells.slice(0, 4).join(", ")}`);
    }
    const maxVolume = target ? Math.max(target.capacityUl, target.maxVolumeUl) : 0;
    if (target && command.volumeUl !== null && maxVolume > 0 && command.volumeUl > maxVolume) {
      messages.push(`Volume exceeds ${target.label} well capacity (${maxVolume.toLocaleString()} uL).`);
    }
  }
  if ((family === "tips" || family === "motion" || family === "labware") && operation && /tip|labware|move|transfer|adapter/i.test(operation) && !targetLabware) {
    messages.push(`${family} command should name a target labware/site.`);
  }

  return {
    ...command,
    name,
    operation,
    family,
    targetLabware,
    wells: uniqueStrings(command.wells.map((well) => well.trim().toUpperCase()).filter(Boolean)),
    liquidClass: command.liquidClass.trim(),
    message: (command.message || "").trim(),
    specs: sanitizeCommandSpecs(command.specs),
    validationIssues: generalValidationIssues(messages),
    validationMessages: messages
  };
}

function generalValidationIssues(messages: string[]): ScriptValidationIssue[] {
  return messages.map((message) => ({ field: "general", message }));
}

function sanitizeCommandSpecs(specs: Record<string, string> | undefined): Record<string, string> {
  return Object.fromEntries(Object.entries(specs || {}).map(([key, value]) => [key.trim(), String(value ?? "").trim()]).filter(([key]) => Boolean(key)));
}

function isHeadAdapterEditorCommand(operation: string): boolean {
  return /(?:^|_)get_head_adapter$|(?:^|_)drop_head_adapter$|mount_adapter|drop_adapter/i.test(operation);
}

function createManualScriptCommand(index: number): ScriptEditorCommand {
  return {
    id: uniqueScriptCommandId("manual"),
    name: `New Command ${index + 1}`,
    operation: "comment",
    family: "comment",
    targetLabware: "",
    wells: [],
    volumeUl: null,
    liquidClass: "",
    message: "",
    specs: {},
    enabled: true,
    dirty: true,
    validationIssues: [],
    validationMessages: []
  };
}

function createScriptCommandFromTemplate(template: CommandToolboxTemplate): ScriptEditorCommand {
  return {
    id: uniqueScriptCommandId(template.id),
    name: template.name,
    operation: template.operation,
    family: template.family,
    targetLabware: template.defaults?.targetLabware || "",
    wells: [...(template.defaults?.wells || [])],
    volumeUl: template.defaults?.volumeUl ?? null,
    liquidClass: template.defaults?.liquidClass || "",
    message: template.defaults?.message || "",
    specs: { ...(template.defaults?.specs || {}) },
    enabled: true,
    dirty: true,
    validationIssues: [],
    validationMessages: []
  };
}

function commandToolboxTemplateById(templateId: string): CommandToolboxTemplate | undefined {
  return COMMAND_TOOLBOX.flatMap((category) => category.commands).find((template) => template.id === templateId);
}

function familyForEditorOperation(operation: string): OperationFamily {
  const lower = operation.toLowerCase();
  if (lower.includes("aspirate") || lower.includes("dispense") || lower.includes("mix")) return "liquid";
  if (lower.includes("tip")) return "tips";
  if (lower.includes("move") || lower.includes("transfer")) return "motion";
  if (lower.includes("wash")) return "wash";
  if (lower.includes("prompt") || lower.includes("user")) return "prompt";
  if (lower.includes("loop") || lower.includes("if") || lower.includes("goto")) return "flow";
  if (lower.includes("labware")) return "labware";
  if (lower.includes("comment")) return "comment";
  return "opaque";
}

function filterScriptEditorCommands(commands: ScriptEditorCommand[], query: string): ScriptEditorCommand[] {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) return commands;
  return commands.filter((command) =>
    normalizeSearchText(
      [
        command.name,
        command.operation,
        command.family,
        command.targetLabware,
        command.liquidClass,
        command.wells.join(" "),
        command.message || "",
        Object.entries(command.specs || {}).flat().join(" "),
        command.enabled ? "enabled" : "disabled",
        ...command.validationMessages
      ].join(" ")
    ).includes(normalizedQuery)
  );
}

function scriptCommandTitle(command: ScriptEditorCommand): string {
  return `${command.operation}${command.targetLabware ? ` -> ${command.targetLabware}` : ""}`;
}

function scriptCommandSummary(command: ScriptEditorCommand): string {
  const bits = [
    command.targetLabware || "no target",
    command.wells.length ? formatWellList(command.wells) : "all wells",
    command.volumeUl === null ? "" : `${command.volumeUl} uL`,
    command.liquidClass,
    command.message,
    Object.entries(command.specs || {}).slice(0, 2).map(([key, value]) => `${key}: ${value}`).join(" · ")
  ].filter(Boolean);
  return bits.join(" · ") || command.operation || "No operation";
}

function parseWellList(value: string): string[] {
  return uniqueStrings(value.split(/[\s,;]+/).map((well) => well.trim().toUpperCase()).filter(Boolean));
}

function formatWellList(wells: string[]): string {
  return wells.join(", ");
}

function compactWellList(wells: string[], visibleCount = 4): string {
  if (wells.length <= visibleCount + 1) return formatWellList(wells);
  if (wells.length > 12) return `${wells.length.toLocaleString()} wells (${wells[0]}-${wells[wells.length - 1]})`;
  return `${wells.slice(0, visibleCount).join(", ")} + ${wells.length - visibleCount} more`;
}

function numberOrNull(value: string): number | null {
  if (!value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function nextCommandSpecKey(specs: Record<string, string>): string {
  let index = 1;
  while (`spec${index}` in specs) index += 1;
  return `spec${index}`;
}

function renameCommandSpec(specs: Record<string, string>, oldKey: string, newKey: string, onUpdate: (patch: ScriptCommandPatch) => void): void {
  const normalizedKey = newKey.trim();
  const nextSpecs: Record<string, string> = {};
  Object.entries(specs).forEach(([key, value]) => {
    if (key === oldKey) {
      if (normalizedKey) nextSpecs[normalizedKey] = value;
      return;
    }
    nextSpecs[key] = value;
  });
  onUpdate({ specs: nextSpecs });
}

function modelCommandIndexForScriptCommand(commands: CommandModel[], scriptCommand: ScriptEditorCommand): number {
  return commands.findIndex((command) => command.id === scriptCommand.sourceCommandId || command.id === scriptCommand.id || command.commandId === scriptCommand.id);
}

function scriptCommandForModelCommand(commands: ScriptEditorCommand[], modelCommand: CommandModel): ScriptEditorCommand | undefined {
  return (
    commands.find((command) => command.sourceCommandId === modelCommand.id || command.id === modelCommand.id || command.id === modelCommand.commandId) ||
    commands.find((command) => command.operation === modelCommand.operation && command.name === modelCommand.name && command.targetLabware === modelCommand.targetLabware)
  );
}

function insertAfterId<T extends { id: string }>(items: T[], id: string, inserted: T): T[] {
  const index = items.findIndex((item) => item.id === id);
  const next = [...items];
  next.splice(index >= 0 ? index + 1 : next.length, 0, inserted);
  return next;
}

function insertBeforeId<T extends { id: string }>(items: T[], id: string, inserted: T): T[] {
  const index = items.findIndex((item) => item.id === id);
  const next = [...items];
  next.splice(index >= 0 ? index : next.length, 0, inserted);
  return next;
}

function moveItemBeforeId<T extends { id: string }>(items: T[], sourceId: string, targetId: string): T[] {
  const sourceIndex = items.findIndex((item) => item.id === sourceId);
  const targetIndex = items.findIndex((item) => item.id === targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return items;
  const next = [...items];
  const [item] = next.splice(sourceIndex, 1);
  const adjustedTargetIndex = sourceIndex < targetIndex ? targetIndex - 1 : targetIndex;
  next.splice(adjustedTargetIndex, 0, item);
  return next;
}

function uniqueScriptCommandId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}`;
}

function installScriptCommandDragGhost(event: DragEvent<HTMLElement>): void {
  const source = event.currentTarget;
  const ghost = source.cloneNode(true) as HTMLElement;
  ghost.classList.add("script-command-drag-ghost");
  ghost.style.position = "fixed";
  ghost.style.top = "-1000px";
  ghost.style.left = "-1000px";
  ghost.style.width = `${source.getBoundingClientRect().width}px`;
  document.body.appendChild(ghost);
  event.dataTransfer.setDragImage(ghost, 24, 24);
  window.setTimeout(() => ghost.remove(), 0);
}

function dataTransferHasType(dataTransfer: DataTransfer, type: string): boolean {
  return Array.from(dataTransfer.types).includes(type);
}

function imageOverridesForExport(overrides: Record<string, string>, assets: HardwareAssetModel[]): HardwareAssetImageOverride[] {
  return Object.entries(overrides).map(([assetId, imageSrc]) => {
    const asset = assets.find((item) => item.id === assetId);
    return {
      assetId,
      assetName: asset?.name || assetId,
      imageSrc,
      mimeType: mimeTypeFromDataUrl(imageSrc) || "image/png",
      fileName: replacementImageFileName(assetId, asset?.name || assetId, imageSrc),
      sourcePath: asset?.sourcePath || asset?.bundlePath || "",
      updatedAt: new Date().toISOString()
    };
  });
}

function restoredSimulatorProjectStateFromArtifacts(artifacts: SourceArtifact[], parsedLabware: LabwareModel[]): RestoredSimulatorProjectState {
  const payload = simulatorProjectPayloadFromArtifacts(artifacts);
  if (!payload) {
    return {
      scriptCommands: [],
      addedObjects: [],
      placementOverrides: [],
      imageOverrides: {},
      activeIndex: null,
      speed: null,
      staticMode: null,
      sceneInteractionMode: null,
      editorTab: null,
      selectedScriptCommandId: ""
    };
  }

  const editor = isRecord(payload.editor) ? payload.editor : {};
  const ui = isRecord(editor.ui) ? editor.ui : {};
  const playback = isRecord(payload.playback) ? payload.playback : {};
  const scriptCommands = Array.isArray(editor.scriptCommands)
    ? editor.scriptCommands
        .map((command) => (isRecord(command) ? scriptEditorCommandFromRecord(command) : null))
        .filter((command): command is ScriptEditorCommand => Boolean(command))
    : [];

  return {
    scriptTitle: stringFromUnknown(ui.scriptTitle) || stringFromUnknown(payload.scriptTitle) || stringFromUnknown(isRecord(payload.protocol) ? payload.protocol.name : ""),
    scriptCommands,
    addedObjects: addedObjectsFromProjectPayload(payload, parsedLabware),
    placementOverrides: placementOverridesFromProjectPayload(payload),
    imageOverrides: imageOverridesFromProjectPayload(payload),
    activeIndex: integerOrNull(playback.activeIndex),
    speed: numberFromUnknown(playback.speed),
    staticMode: typeof playback.staticMode === "boolean" ? playback.staticMode : null,
    sceneInteractionMode: sceneInteractionModeFromUnknown(ui.sceneInteractionMode || playback.sceneInteractionMode),
    editorTab: ui.editorTab === "objects" || ui.editorTab === "script" ? ui.editorTab : null,
    selectedScriptCommandId: stringFromUnknown(ui.selectedScriptCommandId)
  };
}

function simulatorProjectPayloadFromArtifacts(artifacts: SourceArtifact[]): Record<string, unknown> | null {
  const candidates: Record<string, unknown>[] = [];
  for (const artifact of artifacts) {
    if (!artifact.text.trim() || !/\.json$/i.test(artifact.name)) continue;
    try {
      const payload = JSON.parse(artifact.text) as Record<string, unknown>;
      if (isSimulatorProjectPayload(payload)) candidates.push(payload);
    } catch {
      // Ignore non-simulator JSON artifacts.
    }
  }
  // Prefer a full simulator-project save over a leaner edited-command-queue export.
  return candidates.find((payload) => payload.kind === "simulator-project") || candidates[0] || null;
}

function isSimulatorProjectPayload(payload: Record<string, unknown>): boolean {
  // Require our own app marker AND a recognised kind so unrelated JSON (e.g. any
  // file that merely happens to contain an `editor` object) can't hijack
  // restoration when loaded alongside real protocol artifacts.
  if (payload.app !== "tecan-protocol-simulator") return false;
  return payload.kind === "simulator-project" || payload.kind === "edited-command-queue";
}

function addedObjectsForExport(model: ProtocolModel, placedObjects: LabwareModel[]): LabwareModel[] {
  const byKey = new Map<string, LabwareModel>();
  [...placedObjects, ...model.labware.filter((labware) => labware.role === "editor_object" || labware.source === "object-library")].forEach((labware) => {
    byKey.set(labware.id || normalizeSearchText(labware.label), labware);
  });
  return Array.from(byKey.values());
}

function addedObjectsFromProjectPayload(payload: Record<string, unknown>, parsedLabware: LabwareModel[]): LabwareModel[] {
  const editor = isRecord(payload.editor) ? payload.editor : {};
  if (!Array.isArray(editor.addedObjects)) return [];
  const parsedKeys = new Set(parsedLabware.flatMap((labware) => [labware.id, normalizeSearchText(labware.label)]).filter(Boolean));
  return editor.addedObjects
    .map((entry) => (isRecord(entry) ? labwareFromRecord(entry) : null))
    .filter((labware): labware is LabwareModel => Boolean(labware))
    .filter((labware) => !parsedKeys.has(labware.id) && !parsedKeys.has(normalizeSearchText(labware.label)));
}

function labwareFromRecord(record: Record<string, unknown>): LabwareModel | null {
  if (!record.label || !record.id) return null;
  const labware = record as unknown as LabwareModel;
  if (!Array.isArray(labware.wells)) labware.wells = [];
  if (!Array.isArray(labware.hardwareAssetIds)) labware.hardwareAssetIds = [];
  if (!Array.isArray(labware.hardwareAssetNames)) labware.hardwareAssetNames = [];
  return labware;
}

function placementOverridesFromProjectPayload(payload: Record<string, unknown>): ScenePlacementOverride[] {
  const editor = isRecord(payload.editor) ? payload.editor : {};
  if (!Array.isArray(editor.placementOverrides)) return [];
  return editor.placementOverrides
    .map((entry) => (isRecord(entry) ? scenePlacementOverrideFromRecord(entry) : null))
    .filter((override): override is ScenePlacementOverride => Boolean(override));
}

function scenePlacementOverrideFromRecord(record: Record<string, unknown>): ScenePlacementOverride | null {
  const position = isRecord(record.position) ? record.position : {};
  const x = numberFromUnknown(position.x);
  const y = numberFromUnknown(position.y);
  const z = numberFromUnknown(position.z);
  if (x === null || y === null || z === null) return null;
  const label = stringFromUnknown(record.labwareLabel) || stringFromUnknown(record.label);
  if (!label) return null;
  return {
    id: stringFromUnknown(record.id) || label,
    label: stringFromUnknown(record.label) || label,
    labwareLabel: label,
    position: { x, y, z },
    rotationY: numberFromUnknown(record.rotationY) ?? 0,
    source: record.source === "imported" || record.source === "script" || record.source === "user-drag" ? record.source : "imported",
    updatedAt: stringFromUnknown(record.updatedAt) || new Date().toISOString()
  };
}

function upsertScenePlacementOverride(current: ScenePlacementOverride[], override: ScenePlacementOverride): ScenePlacementOverride[] {
  const key = scenePlacementOverrideKey(override);
  const next = current.filter((item) => scenePlacementOverrideKey(item) !== key);
  return [...next, override];
}

function scenePlacementOverrideKey(override: ScenePlacementOverride): string {
  return override.id || normalizeSearchText(override.labwareLabel || override.label);
}

function stringFromUnknown(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sceneInteractionModeFromUnknown(value: unknown): SceneInteractionMode | null {
  return value === "view" || value === "edit" ? value : null;
}

function numberFromUnknown(value: unknown): number | null {
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : Number.NaN;
  return Number.isFinite(number) ? number : null;
}

function integerOrNull(value: unknown): number | null {
  const number = numberFromUnknown(value);
  return number === null ? null : Math.max(0, Math.floor(number));
}

function imageOverridesFromProjectPayload(payload: Record<string, unknown>): Record<string, string> {
  const restored: Record<string, string> = {};
  const editor = isRecord(payload.editor) ? payload.editor : {};
  imageOverrideEntriesFromUnknown(editor.imageOverrides).forEach((override) => {
    if (override.assetId && override.imageSrc) restored[override.assetId] = override.imageSrc;
  });
  imageOverrideEntriesFromUnknown(payload.assetImageOverrides).forEach((override) => {
    if (override.assetId && override.imageSrc) restored[override.assetId] = override.imageSrc;
  });
  return restored;
}

function scriptEditorCommandFromRecord(record: Record<string, unknown>): ScriptEditorCommand {
  const validationIssues = scriptValidationIssuesFromUnknown(record.validationIssues);
  const validationMessages =
    validationIssues.length > 0
      ? validationIssues.map((issue) => issue.message)
      : Array.isArray(record.validationMessages)
        ? record.validationMessages.map((message) => String(message)).filter(Boolean)
        : [];
  return {
    id: String(record.id || uniqueScriptCommandId("restored")),
    sourceCommandId: typeof record.sourceCommandId === "string" ? record.sourceCommandId : undefined,
    name: String(record.name || "Restored Command"),
    operation: String(record.operation || "comment"),
    family: OPERATION_FAMILIES.includes(record.family as OperationFamily) ? (record.family as OperationFamily) : familyForEditorOperation(String(record.operation || "")),
    targetLabware: String(record.targetLabware || ""),
    wells: Array.isArray(record.wells) ? record.wells.map((well) => String(well)).filter(Boolean) : [],
    volumeUl: typeof record.volumeUl === "number" && Number.isFinite(record.volumeUl) ? record.volumeUl : null,
    liquidClass: String(record.liquidClass || ""),
    message: String(record.message || ""),
    specs: specsFromRecord(record.specs),
    enabled: typeof record.enabled === "boolean" ? record.enabled : true,
    dirty: typeof record.dirty === "boolean" ? record.dirty : true,
    validationIssues,
    validationMessages
  };
}

function scriptValidationIssuesFromUnknown(value: unknown): ScriptValidationIssue[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => {
      if (!isRecord(entry) || typeof entry.message !== "string") return null;
      return {
        field: typeof entry.field === "string" ? (entry.field as ScriptValidationIssue["field"]) : "general",
        message: entry.message
      };
    })
    .filter((issue): issue is ScriptValidationIssue => Boolean(issue));
}

function specsFromRecord(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).map(([key, specValue]) => [key, String(specValue ?? "")]));
}

function imageOverrideEntriesFromUnknown(value: unknown): HardwareAssetImageOverride[] {
  if (Array.isArray(value)) {
    return value
      .map((entry) => (isRecord(entry) ? entry : null))
      .filter(Boolean)
      .map((entry) => ({
        assetId: String(entry?.assetId || ""),
        assetName: String(entry?.assetName || entry?.assetId || ""),
        imageSrc: String(entry?.imageSrc || ""),
        mimeType: String(entry?.mimeType || ""),
        fileName: String(entry?.fileName || ""),
        sourcePath: String(entry?.sourcePath || ""),
        updatedAt: String(entry?.updatedAt || "")
      }))
      .filter((entry) => entry.assetId && entry.imageSrc);
  }
  if (isRecord(value)) {
    return Object.entries(value)
      .map(([assetId, imageSrc]) => ({
        assetId,
        assetName: assetId,
        imageSrc: typeof imageSrc === "string" ? imageSrc : isRecord(imageSrc) ? String(imageSrc.imageSrc || "") : "",
        updatedAt: ""
      }))
      .filter((entry) => entry.assetId && entry.imageSrc);
  }
  return [];
}

function replacementImageBundleManifest(overrides: Record<string, string>, assets: HardwareAssetModel[]): Record<string, unknown> {
  const images = imageOverridesForExport(overrides, assets).map(({ imageSrc, ...entry }) => ({
    ...entry,
    bytes: dataUrlByteLength(imageSrc)
  }));
  return {
    format: "tecan-simulator-replacement-images",
    version: 1,
    generatedAt: new Date().toISOString(),
    images
  };
}

async function buildReplacementImageBundle(overrides: Record<string, string>, assets: HardwareAssetModel[]): Promise<Blob | null> {
  const entries = imageOverridesForExport(overrides, assets);
  if (!entries.length) return null;
  const zip = new JSZip();
  zip.file("manifest.json", JSON.stringify(replacementImageBundleManifest(overrides, assets), null, 2) + "\n");
  for (const entry of entries) {
    const blob = await dataUrlToBlob(entry.imageSrc);
    zip.file(`images/${entry.fileName || replacementImageFileName(entry.assetId, entry.assetName, entry.imageSrc)}`, await blob.arrayBuffer());
  }
  return zip.generateAsync({ type: "blob", mimeType: "application/zip" });
}

async function dataUrlToBlob(dataUrl: string): Promise<Blob> {
  const response = await fetch(dataUrl);
  return response.blob();
}

function dataUrlByteLength(dataUrl: string): number {
  const payload = dataUrl.split(",", 2)[1] || "";
  return Math.floor((payload.length * 3) / 4);
}

function mimeTypeFromDataUrl(dataUrl: string): string {
  return dataUrl.match(/^data:([^;,]+)/)?.[1] || "";
}

function replacementImageFileName(assetId: string, assetName: string, imageSrc: string): string {
  const extension = extensionForMimeType(mimeTypeFromDataUrl(imageSrc));
  return `${modelAssetSlug(assetName || assetId || "replacement-image")}${extension}`;
}

function extensionForMimeType(mimeType: string): string {
  if (mimeType.includes("jpeg") || mimeType.includes("jpg")) return ".jpg";
  if (mimeType.includes("webp")) return ".webp";
  if (mimeType.includes("gif")) return ".gif";
  return ".png";
}

function filterObjectLibrary(items: ObjectLibraryItem[], query: string): ObjectLibraryItem[] {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) return items;
  return items.filter((item) =>
    normalizeSearchText([item.name, item.profile, item.componentRole || "", ...item.tags].join(" ")).includes(normalizedQuery)
  );
}

function objectLibrarySummary(item: ObjectLibraryItem): string {
  const profile = item.profile.replace(/-/g, " ");
  const rows = item.rows || 0;
  const cols = item.cols || 0;
  if (rows > 0 && cols > 0) return `${rows}x${cols} · ${profile}`;
  return profile;
}

function labwareFromObjectLibraryDrop(
  item: ObjectLibraryItem,
  placement: SceneObjectDropPlacement,
  existingLabware: LabwareModel[]
): LabwareModel {
  const label = uniqueDroppedObjectLabel(item.name, existingLabware);
  const geometry = objectLibraryGeometryFor(item);
  return {
    id: `editor-${item.id}-${Date.now().toString(36)}-${placement.slotIndex + 1}`,
    label,
    catalogName: item.name,
    geometryName: item.name,
    geometrySource: "inferred",
    role: "editor_object",
    componentRole: item.componentRole,
    source: "object-library",
    location: "Deck",
    position: placement.slotIndex + 1,
    positionExpression: String(placement.slotIndex + 1),
    slotIndex: placement.slotIndex,
    rows: geometry.rows,
    cols: geometry.cols,
    wellShape: geometry.wellShape,
    capacityUl: geometry.capacityUl,
    maxVolumeUl: geometry.maxVolumeUl,
    deadVolumeUl: geometry.deadVolumeUl,
    physicalWidthMm: geometry.physicalWidthMm,
    physicalDepthMm: geometry.physicalDepthMm,
    physicalHeightMm: geometry.physicalHeightMm,
    pitchXMm: geometry.pitchXMm,
    pitchYMm: geometry.pitchYMm,
    wellDiameterMm: geometry.wellDiameterMm,
    wellDepthMm: geometry.wellDepthMm,
    color: geometry.color,
    transform: null,
    wells: wellsForObjectLibraryGeometry(geometry),
    hardwareProfile: item.profile,
    hardwareAssetIds: [],
    hardwareAssetNames: [],
    modelAssetPath: item.modelAssetPath,
    modelAssetFormat: item.modelAssetPath ? "glb" : undefined,
    liquidCapable: geometry.liquidCapable
  };
}

function objectLibraryGeometryFor(item: ObjectLibraryItem): ObjectLibraryGeometry {
  const defaults = objectLibraryGeometryDefaults(item);
  const rows = finiteLibraryNumber(item.rows, defaults.rows);
  const cols = finiteLibraryNumber(item.cols, defaults.cols);
  const wellShape = item.wellShape || defaults.wellShape;
  const capacityUl = finiteLibraryNumber(item.capacityUl, defaults.capacityUl);
  const maxVolumeUl = finiteLibraryNumber(item.maxVolumeUl, defaults.maxVolumeUl || capacityUl);
  return {
    rows,
    cols,
    wellShape,
    capacityUl,
    maxVolumeUl,
    deadVolumeUl: finiteLibraryNumber(item.deadVolumeUl, defaults.deadVolumeUl),
    physicalWidthMm: finiteLibraryNumber(item.physicalWidthMm, defaults.physicalWidthMm),
    physicalDepthMm: finiteLibraryNumber(item.physicalDepthMm, defaults.physicalDepthMm),
    physicalHeightMm: finiteLibraryNumber(item.physicalHeightMm, defaults.physicalHeightMm),
    pitchXMm: finiteLibraryNumber(item.pitchXMm, defaults.pitchXMm),
    pitchYMm: finiteLibraryNumber(item.pitchYMm, defaults.pitchYMm),
    wellDiameterMm: finiteLibraryNumber(item.wellDiameterMm, defaults.wellDiameterMm),
    wellDepthMm: finiteLibraryNumber(item.wellDepthMm, defaults.wellDepthMm),
    color: item.color || defaults.color,
    liquidCapable: item.liquidCapable ?? defaults.liquidCapable
  };
}

function objectLibraryGeometryDefaults(item: ObjectLibraryItem): ObjectLibraryGeometry {
  const roleText = normalizeSearchText(`${item.profile} ${item.componentRole || ""} ${item.name} ${item.tags.join(" ")}`);
  if (item.profile === "tip-box" || roleText.includes("tip")) {
    return {
      rows: 8,
      cols: 12,
      wellShape: "round",
      capacityUl: 0,
      maxVolumeUl: 0,
      deadVolumeUl: 0,
      physicalWidthMm: 127.76,
      physicalDepthMm: 85.48,
      physicalHeightMm: 48,
      pitchXMm: 9,
      pitchYMm: 9,
      wellDiameterMm: 3.8,
      wellDepthMm: 32,
      color: "#e4ebe7",
      liquidCapable: false
    };
  }
  if (item.profile === "tube-holder" || roleText.includes("tube")) {
    return {
      rows: 2,
      cols: 6,
      wellShape: "round",
      capacityUl: 50000,
      maxVolumeUl: 50000,
      deadVolumeUl: 500,
      physicalWidthMm: 128,
      physicalDepthMm: 45,
      physicalHeightMm: 72,
      pitchXMm: 19.5,
      pitchYMm: 19.5,
      wellDiameterMm: 28,
      wellDepthMm: 82,
      color: "#e8ded4",
      liquidCapable: true
    };
  }
  if (item.profile === "reservoir" || roleText.includes("reservoir")) {
    return {
      rows: 1,
      cols: 8,
      wellShape: "square",
      capacityUl: 25000,
      maxVolumeUl: 25000,
      deadVolumeUl: 300,
      physicalWidthMm: 127,
      physicalDepthMm: 43,
      physicalHeightMm: 32,
      pitchXMm: 14.2,
      pitchYMm: 14.2,
      wellDiameterMm: 11,
      wellDepthMm: 27,
      color: "#dfebe1",
      liquidCapable: true
    };
  }
  if (item.profile === "nest" || item.profile === "carrier" || item.profile === "adapter") {
    return {
      rows: 0,
      cols: 0,
      wellShape: "none",
      capacityUl: 0,
      maxVolumeUl: 0,
      deadVolumeUl: 0,
      physicalWidthMm: 140,
      physicalDepthMm: 90,
      physicalHeightMm: 18,
      pitchXMm: 0,
      pitchYMm: 0,
      wellDiameterMm: 0,
      wellDepthMm: 0,
      color: "#e7e2d7",
      liquidCapable: false
    };
  }
  return {
    rows: 8,
    cols: 12,
    wellShape: "round",
    capacityUl: 350,
    maxVolumeUl: 350,
    deadVolumeUl: 8,
    physicalWidthMm: 127.76,
    physicalDepthMm: 85.48,
    physicalHeightMm: 14.35,
    pitchXMm: 9,
    pitchYMm: 9,
    wellDiameterMm: 6.5,
    wellDepthMm: 10.8,
    color: "#dbe7e5",
    liquidCapable: true
  };
}

function finiteLibraryNumber(value: number | undefined, fallback: number): number {
  return Number.isFinite(value) ? Math.max(0, Number(value)) : fallback;
}

function uniqueDroppedObjectLabel(name: string, existingLabware: LabwareModel[]): string {
  const baseLabel = sanitizeScriptTitle(name) || "Deck Object";
  const existingLabels = new Set(existingLabware.map((labware) => normalizeSearchText(labware.label)));
  if (!existingLabels.has(normalizeSearchText(baseLabel))) return baseLabel;
  for (let index = 2; index < 1000; index += 1) {
    const candidate = `${baseLabel} ${index}`;
    if (!existingLabels.has(normalizeSearchText(candidate))) return candidate;
  }
  return `${baseLabel} ${Date.now().toString(36)}`;
}

function wellsForObjectLibraryGeometry(geometry: ObjectLibraryGeometry): WellModel[] {
  const rows = Math.max(0, Math.floor(geometry.rows));
  const cols = Math.max(0, Math.floor(geometry.cols));
  const wells: WellModel[] = [];
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      wells.push({
        id: wellIdFromRowCol(row, col),
        row,
        col,
        volumeUl: 0,
        capacityUl: geometry.capacityUl,
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

function wellIdFromRowCol(row: number, col: number): string {
  return `${wellRowName(row)}${col + 1}`;
}

function wellRowName(index: number): string {
  let remaining = Math.max(0, Math.floor(index));
  let label = "";
  do {
    label = String.fromCharCode(65 + (remaining % 26)) + label;
    remaining = Math.floor(remaining / 26) - 1;
  } while (remaining >= 0);
  return label;
}

function commandStatsFor(model: ProtocolModel): ProtocolStats {
  return model.commands.reduce<ProtocolStats>(
    (stats, command) => {
      stats.commands += 1;
      if (command.family === "liquid") stats.liquidCommands += 1;
      if (command.family === "tips") stats.tipCommands += 1;
      return stats;
    },
    { labware: realWorktableLabware(model.labware).length, commands: 0, liquidCommands: 0, tipCommands: 0 }
  );
}

function geometryCoverageFor(model: ProtocolModel): GeometryCoverageSummary {
  const transformedWithWells = model.labware.filter((labware) => labware.transform && labware.wells.length > 0);
  const commandTargetLabels = uniqueStrings(model.commands.map((command) => command.targetLabware).filter(Boolean));
  const commandedWellTargets = commandTargetLabels
    .map((label) => ({
      label,
      matches: model.labware.filter((labware) => labwareLabelMatches(labware.label, label))
    }))
    .filter((target) => target.matches.some((labware) => labware.wells.length > 0));
  const fallbackCommandedTargets = commandedWellTargets
    .filter((target) => !target.matches.some((labware) => hasExactWellGeometry(labware)))
    .map((target) => target.label);

  return {
    transformedWellLabware: transformedWithWells.length,
    exactTransformedWellLabware: transformedWithWells.filter(hasExactWellGeometry).length,
    commandedWellTargets: commandedWellTargets.length,
    exactCommandedWellTargets: commandedWellTargets.length - fallbackCommandedTargets.length,
    anchoredProtocolLocations: model.labware.filter((labware) => labware.role === "protocol_target" && !labware.transform && Boolean(labware.location)).length,
    fallbackCommandedTargets
  };
}

function hasExactWellGeometry(labware: ProtocolModel["labware"][number]): boolean {
  return Boolean(labware.wells.length && labware.wells.every((well) => well.positionMm && well.positionSource !== "catalog"));
}

function meshInventoryItemsForArtifacts(artifacts: SourceArtifact[]): MeshInventoryItem[] {
  const byKey = new Map<string, MeshInventoryItem>();
  artifacts.filter(isWorktableMeshArtifact).forEach((artifact) => {
    const item = meshInventoryItemForArtifact(artifact);
    const key = item.guid || item.sourcePath || item.name;
    if (!key || byKey.has(key)) return;
    byKey.set(key, item);
  });
  return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name) || a.guid.localeCompare(b.guid));
}

function meshInventoryFor(items: MeshInventoryItem[], lookup: MeshAssetLookup): MeshInventorySummary {
  const foundPaths = new Set(lookup.foundPaths.map(normalizeModelAssetPath));
  const missingItems = items.filter((mesh) => !foundPaths.has(normalizeModelAssetPath(mesh.assetPath)));
  const checking = lookup.status === "checking";
  return {
    items,
    convertedCount: checking ? 0 : items.length - missingItems.length,
    fallbackCount: checking ? 0 : missingItems.length,
    missingItems: checking ? [] : missingItems,
    status: lookup.status,
    error: checking ? "" : lookup.error
  };
}

function isWorktableMeshArtifact(artifact: SourceArtifact): boolean {
  const path = artifact.path || artifact.name;
  return artifact.kind === "worktable-mesh" || path.toLowerCase().endsWith(".xmsh");
}

function meshInventoryItemForArtifact(artifact: SourceArtifact): MeshInventoryItem {
  const sourcePath = artifact.path || artifact.name;
  const guid = normalizeGuid(artifact.meshGuid || stemForPath(sourcePath) || xmlTagText(artifact.text, "GUID"));
  const name = artifact.meshName || xmlTagText(artifact.text, "ObjectName") || guid || pathBasename(sourcePath);
  return {
    guid,
    name,
    sourcePath,
    assetPath: normalizeModelAssetPath(artifact.modelAssetPath || (guid ? `/models/fluent/local/${guid}.glb` : `/models/fluent/local/${modelAssetSlug(name)}.glb`))
  };
}

const MODEL_ASSET_CHECK_TIMEOUT_MS = 8000;
const MODEL_ASSET_CHECK_CONCURRENCY = 6;
const modelAssetCheckCache = new Map<string, ModelAssetCheckStatus>();

async function fetchWithTimeout(input: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

function cachedFoundModelAssetPaths(assetPaths: string[]): string[] {
  return assetPaths
    .map(normalizeModelAssetPath)
    .filter((assetPath) => assetPath && modelAssetCheckCache.get(assetPath) === "found");
}

async function checkModelAssetsThrottled(assetPaths: string[]): Promise<void> {
  const pendingPaths = uniqueStrings(assetPaths.map(normalizeModelAssetPath).filter(Boolean));
  let nextIndex = 0;

  async function worker(): Promise<void> {
    while (nextIndex < pendingPaths.length) {
      const assetPath = pendingPaths[nextIndex];
      nextIndex += 1;
      if (modelAssetCheckCache.has(assetPath)) continue;
      const status = await checkModelAsset(assetPath);
      if (status !== "unknown") modelAssetCheckCache.set(assetPath, status);
    }
  }

  const workerCount = Math.min(MODEL_ASSET_CHECK_CONCURRENCY, pendingPaths.length);
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
}

async function checkModelAsset(assetPath: string): Promise<ModelAssetCheckStatus> {
  const normalizedPath = normalizeModelAssetPath(assetPath);
  if (!normalizedPath) return "missing";
  const cached = modelAssetCheckCache.get(normalizedPath);
  if (cached) return cached;
  try {
    const response = await fetchWithTimeout(normalizedPath, { method: "HEAD", cache: "no-store" }, MODEL_ASSET_CHECK_TIMEOUT_MS);
    if (response.ok) return "found";
    if (response.status !== 405) return "missing";
  } catch {
    return "unknown";
  }

  try {
    const fallbackResponse = await fetchWithTimeout(normalizedPath, { cache: "no-store" }, MODEL_ASSET_CHECK_TIMEOUT_MS);
    return fallbackResponse.ok ? "found" : "missing";
  } catch {
    return "unknown";
  }
}

function normalizeModelAssetPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "";
  if (/^(?:https?:)?\/\//i.test(trimmed)) return trimmed;
  const normalized = `/${trimmed.replace(/^\/+/, "")}`;
  const legacyMesh = normalized.match(/^\/models\/fluent\/([0-9a-f-]+\.glb)$/i);
  if (legacyMesh) return `/models/fluent/local/${legacyMesh[1]}`;
  return normalized;
}

function normalizeGuid(value: string): string {
  const match = value.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  return match?.[0].toLowerCase() || "";
}

function xmlTagText(text: string, tagName: string): string {
  const match = text.match(new RegExp(`<${tagName}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tagName}>`, "i"));
  return match?.[1]?.replace(/\s+/g, " ").trim() || "";
}

function stemForPath(path: string): string {
  const basename = pathBasename(path);
  const index = basename.lastIndexOf(".");
  return index >= 0 ? basename.slice(0, index) : basename;
}

function pathBasename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function modelAssetSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "mesh";
}

function nextLoadRequestId(loadRequestRef: MutableRefObject<number>): number {
  loadRequestRef.current += 1;
  return loadRequestRef.current;
}

function isLatestLoadRequest(loadRequestRef: MutableRefObject<number>, requestId: number): boolean {
  return loadRequestRef.current === requestId;
}

function clampCommandIndex(index: number, commandCount: number): number {
  if (!commandCount) return 0;
  const safeIndex = Number.isFinite(index) ? Math.floor(index) : 0;
  return Math.max(0, Math.min(commandCount - 1, safeIndex));
}

function clampNumber(value: number, min: number, max: number): number {
  const safeValue = Number.isFinite(value) ? value : min;
  return Math.max(min, Math.min(max, safeValue));
}

function formatVolume(volume: number | null): string {
  return volume === null ? "none" : `${volume.toLocaleString()} uL`;
}

function formatTransferDestination(transfer: LabwareTransferModel): string {
  if (transfer.moveToBase) return "<BASE>";
  const site = transfer.site ? ` site ${transfer.site}` : "";
  const anchor = transfer.anchorLabware ? ` on ${transfer.anchorLabware}` : "";
  return `${transfer.location || "unknown"}${site}${anchor}`;
}

function formatWells(wells: string[]): string {
  if (!wells.length) return "all or unspecified";
  if (wells.length <= 8) return wells.join(", ");
  return `${wells.slice(0, 4).join(", ")} + ${wells.length - 4} more`;
}

function formatHeadKind(command: CommandModel): string {
  if (command.headKind === "liha") return "LiHa";
  if (command.headKind === "mca") return command.channelCount ? `MCA${command.channelCount}` : "MCA";
  if (command.channelCount) return `inferred ${command.channelCount} ch`;
  return command.family === "liquid" || command.family === "tips" ? "inferred" : "unknown";
}

function formatActiveTips(command: CommandModel): string {
  const inferredCount = command.selectedTipIndexes.length || command.wells.length || null;
  const count = command.activeTipCount ?? inferredCount;
  return count === null ? "unknown" : count.toLocaleString();
}

function formatTipSpacing(command: CommandModel): string {
  const x = command.tipSpacingXmm;
  const y = command.tipSpacingYmm;
  if (x !== null && y !== null && Math.abs(x - y) > 0.001) return `${formatMm(x)} x ${formatMm(y)} mm`;
  if (x !== null) return `${formatMm(x)} mm`;
  if (y !== null) return `${formatMm(y)} mm`;
  return "unknown";
}

function formatMm(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function formatArtifactCount(count: number): string {
  return `${count.toLocaleString()} artifact${count === 1 ? "" : "s"}`;
}

function formatFileCount(count: number): string {
  return `${count.toLocaleString()} file${count === 1 ? "" : "s"}`;
}

function errorMessageFor(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "Unknown error");
}

function humanReadableProtocolTitle(parsedName: string, selectedSample: SampleDataset | null): string {
  const name = sanitizeScriptTitle(parsedName);
  if (name && !isPathLikeProtocolTitle(name)) return name;
  if (selectedSample?.name) return selectedSample.name;
  if (!name) return "Local Protocol";
  return sanitizeScriptTitle(stemForPath(name)) || "Local Protocol";
}

function humanReadableWorktableName(worktableName: string, selectedSample: SampleDataset | null): string {
  const name = sanitizeScriptTitle(worktableName);
  if (name && !isPathLikeProtocolTitle(name) && !isUuidLikeTitle(name)) return name;
  return selectedSample?.name ? `${selectedSample.name} workspace` : "Local workspace";
}

function isPathLikeProtocolTitle(value: string): boolean {
  return /[\\/]/.test(value) || /\.(xscr|gwl|json|zeia|py)$/i.test(value);
}

function isUuidLikeTitle(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function sanitizeScriptTitle(value: string): string {
  return value.replace(/\s+/g, " ").trim().slice(0, 160);
}

function escapeXmlText(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isEditableShortcutTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}

function labwareLabelMatches(a: string, b: string): boolean {
  return Boolean(a && b && normalizeLabwareLabel(a) === normalizeLabwareLabel(b));
}

function normalizeLabwareLabel(value: string): string {
  return normalizeSearchText(value).replace(/\[[^\]]+\]/g, "").trim();
}

// A transformless labware whose base name matches a physically-placed (transformed)
// instance is a redundant protocol/loop template — e.g. "SampleSourceTube[NumSourceTubes]"
// duplicating the resolved "SampleSourceTube[001..008]", "CollectionDWP[lastcollectplate]"
// duplicating "CollectionDWP[platecount]", or a bare "1x10 ... Tube Runner". These
// inflate the labware count and clutter the command-target options without representing
// any real hardware; the concrete placed instance already stands in for them (and the 3D
// scene already skips them). Parts that are genuinely missing a mesh but ARE placed keep
// their transform, so they are never treated as redundant here.
function isRedundantTemplateLabware(labware: LabwareModel, all: LabwareModel[]): boolean {
  if (labware.transform) return false;
  const base = normalizeLabwareLabel(labware.label);
  if (!base) return false;
  return all.some((other) => other !== labware && Boolean(other.transform) && normalizeLabwareLabel(other.label) === base);
}

function realWorktableLabware(labware: LabwareModel[]): LabwareModel[] {
  return labware.filter((item) => !isRedundantTemplateLabware(item, labware));
}

function normalizeSearchText(value: string): string {
  return value.toLowerCase().replace(/[_/\\-]+/g, " ").replace(/\s+/g, " ").trim();
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function filterCommands(commands: CommandModel[], query: string): CommandModel[] {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) return commands;
  return commands.filter((command) =>
    normalizeSearchText(
      [
        command.name,
        command.operation,
        command.family,
        command.source,
        command.targetLabware || "",
        command.group || "",
        command.liquidClass || "",
        command.wells.join(" ")
      ].join(" ")
    ).includes(normalizedQuery)
  );
}

function shouldCollapseCommandDetail(value: string): boolean {
  const normalized = normalizeSearchText(value);
  return normalized === "not applicable" || normalized === "unknown" || normalized === "none" || normalized === "not found" || normalized === "missing";
}

function formatDevLogEntry(entry: DevLogEntry): string {
  return `[${entry.timestamp}] ${entry.level.toUpperCase()}: ${entry.message}`;
}

function formatDevLogValue(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function hashText(value: string): string {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash.toString(16);
}
