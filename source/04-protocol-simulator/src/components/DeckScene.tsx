import { useEffect, useRef } from "react";
import type { DragEvent as ReactDragEvent } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type {
  CommandModel,
  DiffSeverity,
  HeadKind,
  LabwareModel,
  LabwareTransferModel,
  Matrix3Model,
  ObjectLibraryItem,
  ProtocolModel,
  SceneInteractionMode,
  SceneObjectDragPayload,
  SceneObjectDropPlacement,
  SceneFocusTarget,
  ScenePlacementOverride,
  SceneSnapPlacementTarget,
  SnapSiteModel,
  SnapshotModel,
  ValidationHighlight,
  Vector3Model,
  WellModel
} from "../types";
import { isDeviceComponentText, isStructuralComponentText } from "../data/componentKeywords";

type DeckSceneProps = {
  model: ProtocolModel;
  activeIndex: number;
  focusTarget: SceneFocusTarget | null;
  validationHighlights?: ValidationHighlight[];
  interactionMode?: SceneInteractionMode;
  staticMode?: boolean;
  onFocusTarget?: (focusTarget: SceneFocusTarget | null) => void;
  draggedObject?: SceneObjectDragPayload | null;
  onObjectDrop?: (payload: SceneObjectDragPayload, placement: SceneObjectDropPlacement) => void;
  placementOverrides?: ScenePlacementOverride[];
  onPlacementOverride?: (override: ScenePlacementOverride) => void;
};

type ValidationHighlightLookup = Map<string, ValidationHighlight>;

type RuntimeScene = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  root: THREE.Group;
  lights: THREE.Light[];
  disposed: boolean;
  animationId: number;
  particles: THREE.Object3D[];
  gltfLoader: GLTFLoader;
  modelCache: Map<string, ModelCacheEntry>;
  raycaster: THREE.Raycaster;
  pointer: THREE.Vector2;
  hoverHelper: THREE.Object3D | null;
  selectionHelper: THREE.Object3D | null;
  focusMarkerGroup: THREE.Group | null;
  focusContext: FocusSceneContext | null;
  hoveredObject: THREE.Object3D | null;
  selectedObject: THREE.Object3D | null;
  dragState: SceneDragState | null;
  externalSnapPads: THREE.Group | null;
  externalSnapActiveTargetKey: string | null;
  externalSnapOccupiedKey: string;
  placementOverrides: Map<string, InteractivePlacementOverride>;
  interactionMode: SceneInteractionMode;
  staticMode: boolean;
  onFocusTarget?: (focusTarget: SceneFocusTarget | null) => void;
  onPlacementOverride?: (override: ScenePlacementOverride) => void;
};

type ModelCacheEntry = {
  status: "loading" | "loaded" | "error";
  scene?: THREE.Group;
  error?: unknown;
  // Pending placements awaiting the in-flight load. Called with the loaded
  // source on success, or null on load failure so the group can show a fallback.
  waiters?: Array<(source: THREE.Object3D | null) => void>;
};

type FocusSceneContext = {
  model: ProtocolModel;
  labwarePlacements: Map<string, LabwarePlacement>;
  hasFluentGeometry: boolean;
};

type ModelRenderState = "none" | "loading" | "loaded" | "error";

type VolumeWarning = "overfill" | "underfill" | null;

type VesselProfile = {
  kind: "plate" | "deepwell" | "tube" | "reservoir";
  footprint: "round" | "square";
  radius: number;
  width: number;
  depth: number;
  shellBaseY: number;
  shellHeight: number;
  liquidBaseY: number;
  maxLiquidHeight: number;
  coneHeight: number;
  underfillUl: number;
};

type WellLayout = {
  spanX: number;
  spanZ: number;
  stepX: number;
  stepZ: number;
  radius: number;
  columnsAlongDepth: boolean;
};

type SelectionBounds = {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  width: number;
  depth: number;
  center: THREE.Vector3;
};

type TargetSelection = {
  labware: LabwareModel;
  wells: WellModel[];
  positions: THREE.Vector3[];
  bounds: SelectionBounds | null;
  center: THREE.Vector3;
};

type HeadState = {
  kind: HeadKind;
  label: string;
  channelCount: number | null;
  activeTipCount: number;
  selectedTipIndexes: number[];
  spacingLabel: string;
  tipsMounted: boolean;
  headPosition: THREE.Vector3;
  target: TargetSelection | null;
};

type SceneGeometryContext = {
  hasFluentGeometry: boolean;
  scale: number;
  centerX: number;
  centerY: number;
  minZ: number;
};

type LabwareSceneSize = {
  width: number;
  depth: number;
  height: number;
};

type LabwarePlacement = {
  position: THREE.Vector3;
  rotationY: number;
  size: LabwareSceneSize;
};

type InteractivePlacementOverride = {
  label: string;
  position: THREE.Vector3;
  rotationY: number;
  snapTarget?: SceneSnapPlacementTarget;
};

type SceneDragState = {
  pointerId: number;
  object: THREE.Object3D;
  label: string;
  plane: THREE.Plane;
  // Captured at drag start so an invalid drop can be reverted cleanly.
  originPosition: THREE.Vector3;
  originRotationY: number;
  previousOverride: InteractivePlacementOverride | null;
  snapTarget: SceneSnapTarget | null;
  valid: boolean;
};

type SceneSnapTarget = {
  key: string;
  label: string;
  position: THREE.Vector3;
  rotationY: number;
  slotIndex: number;
  source: "fluent-site" | "deck-slot";
  status: "compatible" | "occupied" | "incompatible";
  parentLabel?: string;
  parentKind?: SceneObjectKind;
  siteGuid?: string;
  siteLocationGroupName?: string;
  compatibleComponentGuids?: string[];
  size: { width: number; depth: number };
};

type SnapDraggedObject = {
  kind: SceneObjectKind;
  componentGuid?: string;
  hardwareProfile?: string;
  roleText: string;
};


type LabwareSceneOverrides = Map<string, LabwareModel>;

type ProtocolAnchorHints = {
  exactLabelScores: Map<string, number>;
  positionScores: Map<number, number>;
};

type SceneObjectKind = "labware" | "tube-rack" | "tip-box" | "carrier" | "nest" | "adapter" | "device" | "waste" | "structural" | "unknown";

type SceneObjectStyle = {
  kind: SceneObjectKind;
  liquidCapable: boolean;
  drySlots: boolean;
  bodyColor: string;
  edgeColor: string;
  accentColor: string;
  labelColor: string;
  labelBackground: string;
  opacity: number;
};

type LabwareSceneHints = LabwareModel & {
  sceneKind?: SceneObjectKind;
  sceneRole?: string;
  componentKind?: string;
  componentRole?: string;
  liquidCapable?: boolean;
  renderAs?: string;
  modelAssetPath?: string;
  modelAssetFormat?: string;
  modelScale?: [number, number, number];
  modelRotationDeg?: [number, number, number];
  modelOffsetMm?: [number, number, number];
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  meshGuid?: string;
  meshName?: string;
  meshSourcePath?: string;
  geometryRole?: string;
  fluentRole?: string;
  functionalGroup?: string;
};

type TransformSceneHints = NonNullable<LabwareModel["transform"]> & Partial<LabwareSceneHints>;

const SLOT_COLUMNS = 6;
const SLOT_ROWS = 3;
const SNAP_SLOT_COUNT = SLOT_COLUMNS * SLOT_ROWS;
const SLOT_WIDTH = 7.2;
const SLOT_DEPTH = 5.2;
const DECK_WIDTH = SLOT_COLUMNS * SLOT_WIDTH + 4;
const DECK_DEPTH = 22;
const HEAD_Y = 4.35;
const SCENE_MM_SCALE = 0.047;
const MAX_BODY_HEIGHT = 1.4;
const MAX_RENDERED_TIPS = 96;
const MAX_LIQUID_PARTICLES = 96;
const MAX_LIQUID_STREAMS = 24;
const MAX_DEBUG_OBJECTS = 160;
const DEBUG_QUERY_KEYS = ["deckDebug", "debugGeometry", "fluentDebug"];
const MODEL_ASSET_EXTENSIONS = new Set([".glb", ".gltf"]);
const MODEL_ASSET_DIRECTORY = "/models/fluent/local";
const SCENE_BACKGROUND = "#3a3e42";
const FLUENT_MESH_YUP_CORRECTION = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
const SCENE_WORLD_UP = new THREE.Vector3(0, 1, 0);
// Standing vessels (tubes/vials) whose mesh footprint is undersized relative to
// their declared labware footprint get a lateral snug-fit so they seat cleanly
// in their holders instead of rattling thin in oversized funnels.
const VESSEL_LATERAL_FIT_MIN = 0.7;
const VESSEL_LATERAL_FIT_MAX = 2.2;
const MODEL_PRIMITIVE_FALLBACK_NAME = "model-primitive-fallback";
// Calm cyan accent used for abstract/no-mesh placeholder labware so it reads as
// "intentionally incomplete" rather than colliding with validation amber/red.
const PLACEHOLDER_FALLBACK_ACCENT = "#5fb0c9";
// Height just above the deck surface where soft contact shadows are laid down.
const CONTACT_SHADOW_Y = 0.02;
let contactShadowTexture: THREE.Texture | null = null;
const NATIVE_MODEL_LARGEST_RATIO_LIMIT = 2.25;
const NATIVE_MODEL_MIDDLE_RATIO_LIMIT = 1.85;
const NATIVE_MODEL_VOLUME_RATIO_LIMIT = 8;

const LIQUID_COLORS = [
  "#3aa6b9",
  "#7fc97f",
  "#f3b562",
  "#d86f6f",
  "#8e7cc3",
  "#59a14f",
  "#edc948",
  "#4e79a7"
];

export function DeckScene({
  model,
  activeIndex,
  focusTarget,
  validationHighlights = [],
  interactionMode = "edit",
  staticMode = true,
  onFocusTarget,
  draggedObject = null,
  onObjectDrop,
  placementOverrides = [],
  onPlacementOverride
}: DeckSceneProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const runtimeRef = useRef<RuntimeScene | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(SCENE_BACKGROUND);
    scene.fog = new THREE.Fog(SCENE_BACKGROUND, 96, 188);

    const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 140);
    camera.position.set(0, 24, 28);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    // Khronos PBR Neutral tone mapping gives a clean, color-faithful CAD look:
    // it compresses highlights without the desaturation/contrast shift of ACES,
    // preserving the tuned categorical labware colors.
    renderer.toneMapping = THREE.NeutralToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.dataset.testid = "deck-canvas";
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(0, 0, 0);
    controls.maxPolarAngle = Math.PI * 0.48;
    controls.minDistance = 10;
    controls.maxDistance = 76;

    const root = new THREE.Group();
    scene.add(root);

    const lights = addLighting(scene);

    const runtime: RuntimeScene = {
      renderer,
      scene,
      camera,
      controls,
      root,
      lights,
      disposed: false,
      animationId: 0,
      particles: [],
      gltfLoader: new GLTFLoader(),
      modelCache: new Map(),
      raycaster: new THREE.Raycaster(),
      pointer: new THREE.Vector2(),
      hoverHelper: null,
      selectionHelper: null,
      focusMarkerGroup: null,
      focusContext: null,
      hoveredObject: null,
      selectedObject: null,
      dragState: null,
      externalSnapPads: null,
      externalSnapActiveTargetKey: null,
      externalSnapOccupiedKey: "",
      placementOverrides: new Map(),
      interactionMode,
      staticMode,
      onFocusTarget,
      onPlacementOverride
    };
    runtimeRef.current = runtime;

    const resizeObserver = new ResizeObserver(() => resizeRenderer(container, runtime));
    resizeObserver.observe(container);
    resizeRenderer(container, runtime);

    const canvas = renderer.domElement;
    const handlePointerMove = (event: PointerEvent) => handleScenePointerMove(runtime, event);
    const handlePointerDown = (event: PointerEvent) => handleScenePointerDown(runtime, event);
    const handlePointerUp = (event: PointerEvent) => handleScenePointerUp(runtime, event);
    const handlePointerLeave = () => clearHover(runtime);
    canvas.addEventListener("pointermove", handlePointerMove);
    canvas.addEventListener("pointerdown", handlePointerDown);
    canvas.addEventListener("pointerup", handlePointerUp);
    canvas.addEventListener("pointercancel", handlePointerUp);
    canvas.addEventListener("pointerleave", handlePointerLeave);

    const clock = new THREE.Clock();
    const animate = () => {
      runtime.animationId = requestAnimationFrame(animate);
      const elapsed = clock.getElapsedTime();
      if (!runtime.staticMode) runtime.particles.forEach((particle, index) => {
        const baseY = particle.userData.baseY || 1;
        const phase = (elapsed * 2.8 + index * 0.13) % 1;
        if (particle.userData.mode === "aspirate") {
          particle.position.y = baseY + 0.12 + phase * 0.9;
        } else if (particle.userData.mode === "dispense") {
          particle.position.y = baseY + 1.02 - phase * 0.86;
        } else if (particle.userData.mode === "labware-transfer") {
          const from = particle.userData.from as THREE.Vector3 | undefined;
          const to = particle.userData.to as THREE.Vector3 | undefined;
          const lift = Number(particle.userData.lift || 2.4);
          if (from && to) {
            particle.position.copy(transferArcPosition(from, to, lift, phase));
          }
        } else {
          const lift = 0.45 + Math.sin(elapsed * 4 + index) * 0.18;
          particle.position.y = baseY + lift;
        }
        if (particle.userData.mode !== "labware-transfer") particle.rotation.y += 0.035;
      });
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      // Mark disposed first so any in-flight GLTF load callbacks bail out instead
      // of rendering on / mutating the torn-down runtime.
      runtime.disposed = true;
      cancelAnimationFrame(runtime.animationId);
      resizeObserver.disconnect();
      canvas.removeEventListener("pointermove", handlePointerMove);
      canvas.removeEventListener("pointerdown", handlePointerDown);
      canvas.removeEventListener("pointerup", handlePointerUp);
      canvas.removeEventListener("pointercancel", handlePointerUp);
      canvas.removeEventListener("pointerleave", handlePointerLeave);
      clearInteractionHelpers(runtime);
      controls.dispose();
      disposeGroup(root);
      disposeModelCache(runtime);
      disposeLights(runtime);
      renderer.dispose();
      renderer.domElement.remove();
      runtimeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.staticMode = staticMode;
    runtime.interactionMode = interactionMode;
    runtime.onFocusTarget = onFocusTarget;
    runtime.onPlacementOverride = onPlacementOverride;
  }, [interactionMode, staticMode, onFocusTarget, onPlacementOverride]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    clearHover(runtime);
    clearSelection(runtime);
    clearExternalSnapPads(runtime);
    clearFocusMarker(runtime);
    syncPlacementOverrides(runtime, placementOverrides);
    disposeGroup(runtime.root);
    runtime.root.clear();
    runtime.particles = [];
    buildScene(runtime, model, activeIndex, focusTarget, staticMode, validationHighlights);
    runtime.renderer.render(runtime.scene, runtime.camera);
    // focusTarget is intentionally NOT a dependency here: it can change from
    // scene interaction, so rebuilding the whole scene on it would thrash. The
    // cheap incremental effect below keeps the focus marker in sync.
  }, [model, activeIndex, staticMode, placementOverrides, validationHighlights]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    updateFocusMarker(runtime, focusTarget);
  }, [focusTarget]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime || draggedObject) return;
    clearExternalSnapPads(runtime);
    runtime.renderer.render(runtime.scene, runtime.camera);
  }, [draggedObject]);

  return (
    <div
      className="deck-scene"
      ref={containerRef}
      data-mode={interactionMode}
      onDragEnter={(event) => handleExternalObjectDragOver(event, runtimeRef.current, model, draggedObject)}
      onDragOver={(event) => handleExternalObjectDragOver(event, runtimeRef.current, model, draggedObject)}
      onDragLeave={(event) => handleExternalObjectDragLeave(event, runtimeRef.current)}
      onDrop={(event) => handleExternalObjectDrop(event, runtimeRef.current, model, draggedObject, onObjectDrop)}
    />
  );
}

function buildScene(
  runtime: RuntimeScene,
  model: ProtocolModel,
  activeIndex: number,
  focusTarget: SceneFocusTarget | null,
  staticMode: boolean,
  validationHighlights: ValidationHighlight[] = []
): void {
  const snapshot = itemAtClampedIndex(model.snapshots, activeIndex);
  const previousSnapshot = activeIndex > 0 ? itemAtClampedIndex(model.snapshots, activeIndex - 1) : undefined;
  const activeCommand = itemAtClampedIndex(model.commands, activeIndex);
  const geometry = sceneGeometryFor(model.labware);
  const showDebugGeometry = geometry.hasFluentGeometry && geometryDebugEnabled();
  const sceneLabware = sceneLabwareFor(model.labware, geometry, showDebugGeometry);
  const labwarePlacements = new Map<string, LabwarePlacement>();
  const labwareOverrides: LabwareSceneOverrides = new Map();
  const activeTargetHasPhysicalMatch = hasPhysicalTargetMatch(model.labware, activeCommand?.targetLabware || "");
  const focusTargetHasPhysicalMatch = hasPhysicalTargetMatch(model.labware, focusTarget?.labwareLabel || "");
  const highlightLookup = validationHighlightLookup(validationHighlights);

  addDeck(runtime.root, geometry.hasFluentGeometry);
  addDeckRulers(runtime.root);
  addDeckGridRuler(runtime.root, geometry, model.labware);

  sceneLabware.forEach((labware) => {
    const placement = labwarePlacement(labware, geometry);
    applyInteractivePlacementOverride(runtime, labware, placement);
    labwarePlacements.set(labware.label, placement);
    addLabware(
      runtime,
      runtime.root,
      labware,
      placement,
      geometry,
      snapshot,
      activeCommand,
      focusTarget,
      activeTargetHasPhysicalMatch,
      focusTargetHasPhysicalMatch,
      validationHighlightForLabware(highlightLookup, labware)
    );
  });

  addAnchoredProtocolTargets(runtime, model, geometry, snapshot, activeCommand, focusTarget, labwarePlacements, labwareOverrides, activeTargetHasPhysicalMatch, focusTargetHasPhysicalMatch);
  if (showDebugGeometry) addDiffMarkers(runtime.root, model, labwarePlacements);
  runtime.focusContext = { model, labwarePlacements, hasFluentGeometry: geometry.hasFluentGeometry };
  updateFocusMarker(runtime, focusTarget, false);
  if (showDebugGeometry) {
    addFluentGeometryDebug(runtime.root, model.labware, geometry, labwarePlacements);
  }
  addLabwareTransferMotion(runtime.root, model, activeCommand, previousSnapshot, snapshot, geometry, labwarePlacements, labwareOverrides, runtime.particles, staticMode);
  addRobotGantry(runtime.root, model, activeCommand, snapshot, geometry, labwarePlacements, labwareOverrides, runtime.particles, staticMode);
  const target = targetSelectionFor(model, activeCommand, labwarePlacements, geometry, labwareOverrides);
  if (!frameCameraForActiveTarget(runtime, target, labwarePlacements)) {
    frameCameraForSceneOverview(runtime, labwarePlacements);
  }
}


function applyInteractivePlacementOverride(runtime: RuntimeScene, labware: LabwareModel, placement: LabwarePlacement): void {
  const override = placementOverrideForLabel(runtime.placementOverrides, labware.label);
  if (!override) return;
  placement.position.copy(override.position);
  placement.rotationY = override.rotationY;
}

function placementOverrideForLabel(overrides: Map<string, InteractivePlacementOverride>, label: string): InteractivePlacementOverride | null {
  return overrides.get(label) || Array.from(overrides.values()).find((override) => labwareLabelMatches(override.label, label)) || null;
}

function syncPlacementOverrides(runtime: RuntimeScene, overrides: ScenePlacementOverride[]): void {
  runtime.placementOverrides.clear();
  overrides.forEach((override) => {
    const label = override.labwareLabel || override.label;
    if (!label) return;
    runtime.placementOverrides.set(label, {
      label,
      position: new THREE.Vector3(override.position.x, override.position.y, override.position.z),
      rotationY: override.rotationY,
      snapTarget: override.snapTarget
    });
  });
}

function markSceneInteractive(object: THREE.Object3D, labware: LabwareModel, placement?: LabwarePlacement, draggable = true): void {
  const slotIndex = labware.position ? labware.position - 1 : labware.slotIndex;
  object.userData.sceneSelectable = true;
  object.userData.sceneDraggable = draggable;
  object.userData.labwareId = labware.id;
  object.userData.selectionLabel = labware.label;
  object.userData.selectionKind = "labware";
  object.userData.slotIndex = slotIndex;
  object.userData.selectionOwner = object;
  object.userData.selectionSize = placement?.size;
  object.userData.selectionRotationY = placement?.rotationY;
  object.traverse((child) => {
    child.userData.sceneSelectable = true;
    child.userData.sceneDraggable = draggable;
    child.userData.labwareId = labware.id;
    child.userData.selectionLabel = labware.label;
    child.userData.selectionKind = "labware";
    child.userData.slotIndex = slotIndex;
    child.userData.selectionOwner = object;
  });
}

function handleScenePointerMove(runtime: RuntimeScene, event: PointerEvent): void {
  if (runtime.dragState) {
    updateSceneDrag(runtime, event);
    return;
  }

  const hit = interactiveObjectFromPointer(runtime, event);
  if (hit !== runtime.hoveredObject) {
    runtime.hoveredObject = hit;
    setHoverHelper(runtime, hit);
    const focusTarget = hit ? focusTargetForInteractiveObject(hit) : null;
    if (focusTarget && !runtime.selectedObject) runtime.onFocusTarget?.(focusTarget);
  }
  runtime.renderer.domElement.style.cursor = hit ? sceneCursorForHit(runtime, hit) : "";
}

function handleScenePointerDown(runtime: RuntimeScene, event: PointerEvent): void {
  if (event.button !== 0) return;
  const hit = interactiveObjectFromPointer(runtime, event);
  if (!hit) {
    if (runtime.selectedObject) {
      clearSelection(runtime);
      runtime.onFocusTarget?.(null);
      runtime.renderer.domElement.style.cursor = "";
      event.preventDefault();
    }
    return;
  }
  event.preventDefault();
  runtime.selectedObject = hit;
  setSelectionHelper(runtime, hit);
  const focusTarget = focusTargetForInteractiveObject(hit);
  if (focusTarget) runtime.onFocusTarget?.(focusTarget);
  if (runtime.interactionMode !== "edit" || !hit.userData.sceneDraggable) {
    runtime.renderer.domElement.style.cursor = hit ? sceneCursorForHit(runtime, hit) : "";
    return;
  }
  startSceneDrag(runtime, event, hit);
}

function handleScenePointerUp(runtime: RuntimeScene, event: PointerEvent): void {
  const drag = runtime.dragState;
  if (!drag) return;
  try {
    runtime.renderer.domElement.releasePointerCapture(drag.pointerId);
  } catch {
    // Pointer capture can already be released by the browser during cancel/leave.
  }
  const focusTarget = focusTargetForInteractiveObject(drag.object);
  if (drag.valid) {
    const override = commitSceneDragSnap(runtime, drag);
    if (override) runtime.onPlacementOverride?.(scenePlacementOverrideForDragObject(drag.object, override));
  } else {
    revertSceneDrag(runtime, drag);
  }
  clearExternalSnapPads(runtime);
  runtime.dragState = null;
  runtime.controls.enabled = true;
  runtime.renderer.domElement.style.cursor = runtime.hoveredObject ? sceneCursorForHit(runtime, runtime.hoveredObject) : "";
  if (focusTarget) runtime.onFocusTarget?.(focusTarget);
  event.preventDefault();
}

function commitSceneDragSnap(runtime: RuntimeScene, drag: SceneDragState): InteractivePlacementOverride | null {
  if (!drag.snapTarget) return null;
  drag.object.position.copy(drag.snapTarget.position);
  drag.object.rotation.y = drag.snapTarget.rotationY;
  const storedWorldPosition = drag.object.getWorldPosition(new THREE.Vector3());
  const override = {
    label: drag.label,
    position: storedWorldPosition,
    rotationY: drag.snapTarget.rotationY,
    snapTarget: sceneSnapPlacementTargetFor(drag.snapTarget)
  };
  runtime.placementOverrides.set(drag.label, override);
  runtime.renderer.render(runtime.scene, runtime.camera);
  return override;
}

// Returns the part to where it started and restores the pre-drag override state, so
// an incompatible drop leaves no trace (no committed move, no React rebuild).
function revertSceneDrag(runtime: RuntimeScene, drag: SceneDragState): void {
  drag.object.position.copy(drag.originPosition);
  drag.object.rotation.y = drag.originRotationY;
  if (drag.previousOverride) {
    runtime.placementOverrides.set(drag.label, {
      label: drag.previousOverride.label,
      position: drag.previousOverride.position.clone(),
      rotationY: drag.previousOverride.rotationY,
      snapTarget: drag.previousOverride.snapTarget
    });
  } else {
    runtime.placementOverrides.delete(drag.label);
  }
  runtime.renderer.render(runtime.scene, runtime.camera);
}

function sceneCursorForHit(runtime: RuntimeScene, object: THREE.Object3D): string {
  return runtime.interactionMode === "edit" && object.userData.sceneDraggable ? "grab" : "pointer";
}

function scenePlacementOverrideForDragObject(object: THREE.Object3D, override: InteractivePlacementOverride): ScenePlacementOverride {
  const labwareId = String(object.userData.labwareId || override.label);
  return {
    id: labwareId,
    label: override.label,
    labwareLabel: override.label,
    position: {
      x: override.position.x,
      y: override.position.y,
      z: override.position.z
    },
    rotationY: override.rotationY,
    source: "user-drag",
    snapTarget: override.snapTarget,
    updatedAt: new Date().toISOString()
  };
}

function interactiveObjectFromPointer(runtime: RuntimeScene, event: PointerEvent): THREE.Object3D | null {
  const rect = runtime.renderer.domElement.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1;
  const y = -(((event.clientY - rect.top) / Math.max(1, rect.height)) * 2 - 1);
  runtime.pointer.set(x, y);
  runtime.raycaster.setFromCamera(runtime.pointer, runtime.camera);
  const intersections = runtime.raycaster.intersectObjects(runtime.root.children, true);
  for (const intersection of intersections) {
    const owner = selectableOwnerFor(intersection.object);
    if (owner) return owner;
  }
  return null;
}

function selectableOwnerFor(object: THREE.Object3D): THREE.Object3D | null {
  let current: THREE.Object3D | null = object;
  while (current) {
    const owner = current.userData.selectionOwner;
    if (owner instanceof THREE.Object3D) return owner;
    if (current.userData.sceneSelectable) return current;
    current = current.parent;
  }
  return null;
}

function focusTargetForInteractiveObject(object: THREE.Object3D): SceneFocusTarget | null {
  const label = String(object.userData.selectionLabel || "").trim();
  if (!label) return null;
  const slotIndex = typeof object.userData.slotIndex === "number" ? object.userData.slotIndex : undefined;
  return {
    kind: "labware",
    label,
    labwareLabel: label,
    slotIndex
  };
}

function startSceneDrag(runtime: RuntimeScene, event: PointerEvent, object: THREE.Object3D): void {
  const label = String(object.userData.selectionLabel || "").trim();
  if (!label) return;
  const objectWorldPosition = object.getWorldPosition(new THREE.Vector3());
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -objectWorldPosition.y);
  const targets = snapTargetsForScene(runtime, runtime.focusContext?.model, { excludeLabel: label });
  const activeTarget = snapTargetForPointerEvent(runtime, event, targets);
  runtime.controls.enabled = false;
  runtime.renderer.domElement.setPointerCapture(event.pointerId);
  runtime.renderer.domElement.style.cursor = "grabbing";
  renderExternalSnapPads(runtime, targets, activeTarget?.key || null);
  const existingOverride = placementOverrideForLabel(runtime.placementOverrides, label);
  runtime.dragState = {
    pointerId: event.pointerId,
    object,
    label,
    plane,
    originPosition: object.position.clone(),
    originRotationY: object.rotation.y,
    previousOverride: existingOverride
      ? {
          label: existingOverride.label,
          position: existingOverride.position.clone(),
          rotationY: existingOverride.rotationY,
          snapTarget: existingOverride.snapTarget
        }
      : null,
    snapTarget: activeTarget,
    valid: activeTarget !== null
  };
}

function updateSceneDrag(runtime: RuntimeScene, event: PointerEvent): void {
  const drag = runtime.dragState;
  if (!drag) return;
  const targets = snapTargetsForScene(runtime, runtime.focusContext?.model, { excludeLabel: drag.label });
  drag.snapTarget = snapTargetForPointerEvent(runtime, event, targets);
  drag.valid = drag.snapTarget !== null;
  renderExternalSnapPads(runtime, targets, drag.snapTarget?.key || null);
  runtime.renderer.domElement.style.cursor = drag.valid ? "grabbing" : "not-allowed";
}

function pointerPlaneIntersection(runtime: RuntimeScene, event: { clientX: number; clientY: number }, plane: THREE.Plane): THREE.Vector3 | null {
  const rect = runtime.renderer.domElement.getBoundingClientRect();
  runtime.pointer.set(((event.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1, -(((event.clientY - rect.top) / Math.max(1, rect.height)) * 2 - 1));
  runtime.raycaster.setFromCamera(runtime.pointer, runtime.camera);
  return runtime.raycaster.ray.intersectPlane(plane, new THREE.Vector3()) || null;
}

function setHoverHelper(runtime: RuntimeScene, object: THREE.Object3D | null): void {
  removeOutlineObject(runtime.hoverHelper);
  runtime.hoverHelper = null;
  if (!object || object === runtime.selectedObject) return;
  runtime.hoverHelper = buildSelectionOutline(object, "#2fd2dd", "hover-selection-outline");
}

function setSelectionHelper(runtime: RuntimeScene, object: THREE.Object3D | null): void {
  removeOutlineObject(runtime.selectionHelper);
  runtime.selectionHelper = null;
  if (!object) return;
  runtime.selectionHelper = buildSelectionOutline(object, "#f2b134", "active-selection-outline");
}

// Builds a true edge outline that traces the object's actual mesh silhouette
// (not just a bounding box) and parents it to the object so it follows drag and
// rotation without per-frame updates. High-poly meshes are skipped per-mesh to
// keep hover responsive.
function buildSelectionOutline(object: THREE.Object3D, color: string, name: string): THREE.Object3D {
  const group = new THREE.Group();
  group.name = name;
  group.raycast = () => undefined;
  const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.95 });
  object.updateWorldMatrix(true, true);
  const objectInverse = new THREE.Matrix4().copy(object.matrixWorld).invert();
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (!mesh.isMesh || !mesh.geometry) return;
    if ((mesh.geometry.attributes.position?.count ?? 0) > 120000) return;
    mesh.updateWorldMatrix(true, false);
    const line = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 30), material);
    line.applyMatrix4(new THREE.Matrix4().multiplyMatrices(objectInverse, mesh.matrixWorld));
    line.renderOrder = 7;
    line.raycast = () => undefined;
    group.add(line);
  });
  object.add(group);
  return group;
}

function removeOutlineObject(outline: THREE.Object3D | null): void {
  if (!outline) return;
  outline.parent?.remove(outline);
  disposeObject(outline);
}

function clearHover(runtime: RuntimeScene): void {
  runtime.hoveredObject = null;
  removeOutlineObject(runtime.hoverHelper);
  runtime.hoverHelper = null;
  runtime.renderer.domElement.style.cursor = runtime.selectedObject ? sceneCursorForHit(runtime, runtime.selectedObject) : "";
}

function clearSelection(runtime: RuntimeScene): void {
  const drag = runtime.dragState;
  if (drag) {
    // A rebuild/unmount can interrupt an active drag; release the pointer capture
    // so the browser doesn't keep routing events to a soon-to-be-disposed object.
    try {
      runtime.renderer.domElement.releasePointerCapture(drag.pointerId);
    } catch {
      // Capture may already be released by the browser.
    }
  }
  runtime.selectedObject = null;
  runtime.dragState = null;
  runtime.controls.enabled = true;
  removeOutlineObject(runtime.selectionHelper);
  runtime.selectionHelper = null;
}

function updateFocusMarker(runtime: RuntimeScene, focusTarget: SceneFocusTarget | null, render = true): void {
  clearFocusMarker(runtime);
  const context = runtime.focusContext;
  if (!focusTarget || !context) {
    if (render) runtime.renderer.render(runtime.scene, runtime.camera);
    return;
  }

  const group = new THREE.Group();
  group.name = "focus-marker-overlay";
  addFocusMarker(group, context.model, focusTarget, context.labwarePlacements, context.hasFluentGeometry);
  if (!group.children.length) {
    disposeObject(group);
    if (render) runtime.renderer.render(runtime.scene, runtime.camera);
    return;
  }

  runtime.focusMarkerGroup = group;
  runtime.scene.add(group);
  if (render) runtime.renderer.render(runtime.scene, runtime.camera);
}

function clearFocusMarker(runtime: RuntimeScene): void {
  if (!runtime.focusMarkerGroup) return;
  runtime.scene.remove(runtime.focusMarkerGroup);
  disposeObject(runtime.focusMarkerGroup);
  runtime.focusMarkerGroup = null;
}

function clearInteractionHelpers(runtime: RuntimeScene): void {
  clearHover(runtime);
  clearSelection(runtime);
  clearExternalSnapPads(runtime);
  clearFocusMarker(runtime);
}

function handleExternalObjectDragOver(
  event: ReactDragEvent<HTMLDivElement>,
  runtime: RuntimeScene | null,
  model: ProtocolModel,
  payload: SceneObjectDragPayload | null
): void {
  if (!runtime || !payload || runtime.interactionMode !== "edit") return;
  event.preventDefault();
  event.stopPropagation();
  const targets = snapTargetsForScene(runtime, model, { draggedObject: payload });
  const activeTarget = snapTargetForDragEvent(runtime, event.nativeEvent, targets);
  renderExternalSnapPads(runtime, targets, activeTarget?.key || null);
  event.dataTransfer.dropEffect = activeTarget === null ? "none" : "copy";
  runtime.renderer.domElement.style.cursor = activeTarget === null ? "not-allowed" : "copy";
}

function handleExternalObjectDragLeave(event: ReactDragEvent<HTMLDivElement>, runtime: RuntimeScene | null): void {
  if (!runtime) return;
  const nextTarget = event.relatedTarget;
  if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
  clearExternalSnapPads(runtime);
  runtime.renderer.domElement.style.cursor = "";
  runtime.renderer.render(runtime.scene, runtime.camera);
}

function handleExternalObjectDrop(
  event: ReactDragEvent<HTMLDivElement>,
  runtime: RuntimeScene | null,
  model: ProtocolModel,
  payload: SceneObjectDragPayload | null,
  onObjectDrop: DeckSceneProps["onObjectDrop"]
): void {
  if (!runtime || !payload || runtime.interactionMode !== "edit") return;
  event.preventDefault();
  event.stopPropagation();
  const targets = snapTargetsForScene(runtime, model, { draggedObject: payload });
  const target = snapTargetForDragEvent(runtime, event.nativeEvent, targets);
  clearExternalSnapPads(runtime);
  runtime.renderer.domElement.style.cursor = "";
  runtime.renderer.render(runtime.scene, runtime.camera);
  if (!target) return;
  onObjectDrop?.(payload, {
    position: { x: target.position.x, y: target.position.y, z: target.position.z },
    slotIndex: target.slotIndex,
    rotationY: target.rotationY,
    snapTarget: sceneSnapPlacementTargetFor(target)
  });
}

function sceneSnapPlacementTargetFor(target: SceneSnapTarget): SceneSnapPlacementTarget {
  return {
    key: target.key,
    label: target.label,
    source: target.source,
    parentLabel: target.parentLabel,
    parentKind: target.parentKind,
    siteGuid: target.siteGuid,
    siteLocationGroupName: target.siteLocationGroupName,
    compatibleComponentGuids: target.compatibleComponentGuids
  };
}

function snapTargetForDragEvent(runtime: RuntimeScene, event: DragEvent, targets: SceneSnapTarget[]): SceneSnapTarget | null {
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  const pointerWorld = pointerPlaneIntersection(runtime, event, plane);
  if (!pointerWorld) return null;
  return snapTargetContainingPoint(pointerWorld, targets);
}

function snapTargetForPointerEvent(runtime: RuntimeScene, event: PointerEvent, targets: SceneSnapTarget[]): SceneSnapTarget | null {
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  const pointerWorld = pointerPlaneIntersection(runtime, event, plane);
  if (!pointerWorld) return null;
  return snapTargetContainingPoint(pointerWorld, targets);
}

function renderExternalSnapPads(runtime: RuntimeScene, targets: SceneSnapTarget[], activeTargetKey: string | null): void {
  const occupiedKey = targets.map((target) => target.key).sort().join(",");
  if (runtime.externalSnapPads && runtime.externalSnapActiveTargetKey === activeTargetKey && runtime.externalSnapOccupiedKey === occupiedKey) return;
  clearExternalSnapPads(runtime);

  const group = new THREE.Group();
  group.name = "external-object-snap-pads";

  targets.forEach((target) => {
    const isActive = target.key === activeTargetKey;
    const style = snapTargetVisualStyle(target, isActive);
    const pad = new THREE.Mesh(
      new THREE.BoxGeometry(target.size.width, 0.08, target.size.depth),
      new THREE.MeshStandardMaterial({
        color: style.color,
        emissive: style.color,
        emissiveIntensity: style.emissiveIntensity,
        transparent: true,
        opacity: style.opacity,
        roughness: 0.42,
        depthWrite: false
      })
    );
    pad.position.set(target.position.x, 0.08, target.position.z);
    pad.rotation.y = target.rotationY;
    pad.userData.snapTargetKey = target.key;
    group.add(pad);

    const edge = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(target.size.width * 1.03, 0.1, target.size.depth * 1.03)),
      new THREE.LineBasicMaterial({ color: style.edgeColor, transparent: true, opacity: style.edgeOpacity })
    );
    edge.position.copy(pad.position);
    edge.rotation.y = pad.rotation.y;
    group.add(edge);

    if (isActive) addActiveSnapTargetMarker(group, target, style.color);
  });

  runtime.externalSnapPads = group;
  runtime.externalSnapActiveTargetKey = activeTargetKey;
  runtime.externalSnapOccupiedKey = occupiedKey;
  runtime.scene.add(group);
  runtime.renderer.render(runtime.scene, runtime.camera);
}

function snapTargetVisualStyle(
  target: SceneSnapTarget,
  active: boolean
): { color: string; edgeColor: string; opacity: number; edgeOpacity: number; emissiveIntensity: number } {
  if (active) {
    return { color: "#f2b134", edgeColor: "#fff2b8", opacity: 0.7, edgeOpacity: 0.96, emissiveIntensity: 0.32 };
  }
  if (target.status === "occupied") {
    return { color: "#7b8084", edgeColor: "#9ca3a8", opacity: 0.18, edgeOpacity: 0.28, emissiveIntensity: 0.02 };
  }
  if (target.status === "incompatible") {
    return { color: "#a7423a", edgeColor: "#d06b61", opacity: 0.2, edgeOpacity: 0.38, emissiveIntensity: 0.04 };
  }
  return target.source === "fluent-site"
    ? { color: "#128979", edgeColor: "#5ef0d2", opacity: 0.34, edgeOpacity: 0.62, emissiveIntensity: 0.1 }
    : { color: "#19747b", edgeColor: "#78d5dc", opacity: 0.28, edgeOpacity: 0.52, emissiveIntensity: 0.08 };
}

function addActiveSnapTargetMarker(group: THREE.Group, target: SceneSnapTarget, color: string): void {
  const radius = Math.max(target.size.width, target.size.depth) * 0.64;
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(radius * 0.78, radius, 48),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.72, side: THREE.DoubleSide, depthWrite: false })
  );
  ring.position.set(target.position.x, 0.18, target.position.z);
  ring.rotation.x = -Math.PI / 2;
  ring.renderOrder = 9;
  group.add(ring);

  const mast = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(0.12, 0.9, 0.12)),
    new THREE.LineBasicMaterial({ color: "#fff2b8", transparent: true, opacity: 0.86 })
  );
  mast.position.set(target.position.x, 0.58, target.position.z);
  mast.renderOrder = 9;
  group.add(mast);

  const label = makeTextSprite("Drop here", "#3a2a05", "rgba(255,242,184,0.92)");
  label.position.set(target.position.x, 1.18, target.position.z);
  label.scale.set(2.25, 0.42, 1);
  label.renderOrder = 10;
  group.add(label);
}

function clearExternalSnapPads(runtime: RuntimeScene): void {
  if (runtime.externalSnapPads) {
    runtime.scene.remove(runtime.externalSnapPads);
    disposeObject(runtime.externalSnapPads);
  }
  runtime.externalSnapPads = null;
  runtime.externalSnapActiveTargetKey = null;
  runtime.externalSnapOccupiedKey = "";
}

function snapTargetsForScene(
  runtime: RuntimeScene,
  model: ProtocolModel | null | undefined,
  options: { excludeLabel?: string; draggedObject?: SceneObjectDragPayload | null } = {}
): SceneSnapTarget[] {
  const context = runtime.focusContext;
  const sourceModel = context?.model || model;
  const dragged = snapDraggedObjectFor(context, sourceModel, options.excludeLabel || "", options.draggedObject || null);
  if (context && sourceModel) {
    const geometry = sceneGeometryFor(sourceModel.labware);
    const fluentTargets = geometry.hasFluentGeometry ? fluentSnapTargetsForContext(context, geometry, options.excludeLabel || "", dragged) : [];
    if (fluentTargets.length) return fluentTargets;
  }
  return deckSlotSnapTargets(model, options.excludeLabel || "", dragged);
}

function fluentSnapTargetsForContext(
  context: FocusSceneContext,
  geometry: SceneGeometryContext,
  excludeLabel = "",
  dragged: SnapDraggedObject | null = null
): SceneSnapTarget[] {
  const occupied = occupiedFluentSnapTargetKeys(context, excludeLabel);
  const targets: SceneSnapTarget[] = [];
  context.model.labware.forEach((labware) => {
    if (!labware.transform || !labware.snapSites?.length) return;
    if (excludeLabel && labwareLabelMatches(labware.label, excludeLabel)) return;
    const style = sceneObjectStyleFor(labware);
    if (!fluentParentCanHostSnapTargets(style)) return;
    const placement = placementForLabwareLabel(context.labwarePlacements, labware.label);
    if (!placement) return;
    const siteSize = snapSitePadSize(labware.snapSites, labware, geometry, placement);
    labware.snapSites.forEach((site, index) => {
      if (!site.positionMm || !isFiniteVector(site.positionMm)) return;
      const key = fluentSnapTargetKey(labware.label, site);
      const position = fluentLocalSiteToThree(labware, site.positionMm, geometry);
      if (!position) return;
      const target: SceneSnapTarget = {
        key,
        label: `${labware.label} site ${site.index ?? index + 1}`,
        position,
        rotationY: placement.rotationY,
        slotIndex: 1000 + targets.length,
        source: "fluent-site",
        status: "compatible",
        parentLabel: labware.label,
        parentKind: style.kind,
        siteGuid: site.siteGuid,
        siteLocationGroupName: site.siteLocationGroupName,
        compatibleComponentGuids: site.compatibleComponentGuids,
        size: siteSize
      };
      if (occupied.has(key)) target.status = "occupied";
      else if (!snapTargetCompatibleWithDraggedObject(target, dragged)) target.status = "incompatible";
      targets.push(target);
    });
  });
  return targets;
}

function fluentParentCanHostSnapTargets(style: SceneObjectStyle): boolean {
  return style.kind === "carrier" || style.kind === "nest" || style.kind === "adapter" || style.kind === "structural";
}

function occupiedFluentSnapTargetKeys(context: FocusSceneContext, excludeLabel = ""): Set<string> {
  const occupied = new Set<string>();
  const geometry = sceneGeometryFor(context.model.labware);
  context.model.labware.forEach((labware) => {
    if (excludeLabel && labwareLabelMatches(labware.label, excludeLabel)) return;
    const parentLabels = labware.transform?.parentLabels || [];
    const parentLabel = parentLabels[parentLabels.length - 1];
    const siteGuid = labware.transform?.siteGuid;
    if (parentLabel && siteGuid) occupied.add(fluentSnapTargetKey(parentLabel, { siteGuid, positionMm: { x: 0, y: 0, z: 0 } }));
  });

  context.labwarePlacements.forEach((placement, label) => {
    if (excludeLabel && labwareLabelMatches(label, excludeLabel)) return;
    context.model.labware.forEach((parent) => {
      if (!parent.snapSites?.length || !parent.transform) return;
      parent.snapSites.forEach((site) => {
        const position = fluentLocalSiteToThree(parent, site.positionMm, geometry);
        if (position && position.distanceToSquared(placement.position) < 0.18) occupied.add(fluentSnapTargetKey(parent.label, site));
      });
    });
  });
  return occupied;
}

function fluentSnapTargetKey(parentLabel: string, site: Pick<SnapSiteModel, "index" | "siteGuid" | "positionMm">): string {
  const siteKey = site.siteGuid || `site-${site.index ?? `${site.positionMm.x}:${site.positionMm.y}:${site.positionMm.z}`}`;
  return `fluent:${normalizeLabwareLabel(parentLabel)}:${normalizeGuidText(siteKey)}`;
}

function normalizeGuidText(value: string): string {
  return value.trim().replace(/[{}]/g, "").toLowerCase();
}

function fluentLocalSiteToThree(labware: LabwareModel, sitePosition: Vector3Model, geometry: SceneGeometryContext): THREE.Vector3 | null {
  const transform = labware.transform;
  if (!transform || !isFiniteVector(sitePosition)) return null;
  return fluentToThree(addFluentVectors(transform.positionMm, rotateFluentVector(transform.orientation, sitePosition)), geometry);
}

function snapSitePadSize(sites: SnapSiteModel[], labware: LabwareModel, geometry: SceneGeometryContext, placement: LabwarePlacement): { width: number; depth: number } {
  const positions = sites
    .map((site) => fluentLocalSiteToThree(labware, site.positionMm, geometry))
    .filter((position): position is THREE.Vector3 => Boolean(position));
  const spacing = nearestSnapSiteSpacing(positions);
  if (spacing) {
    const edge = clamp(spacing * 0.62, 0.48, 2.8);
    return { width: edge, depth: edge };
  }
  return {
    width: clamp(placement.size.width * 0.32, 0.55, 2.8),
    depth: clamp(placement.size.depth * 0.32, 0.45, 2.2)
  };
}

function nearestSnapSiteSpacing(positions: THREE.Vector3[]): number | null {
  if (positions.length < 2) return null;
  let best = Number.POSITIVE_INFINITY;
  positions.forEach((position, index) => {
    for (let otherIndex = index + 1; otherIndex < positions.length; otherIndex += 1) {
      const distance = position.distanceTo(positions[otherIndex]);
      if (distance > 0.001 && distance < best) best = distance;
    }
  });
  return Number.isFinite(best) ? best : null;
}

function snapDraggedObjectFor(
  context: FocusSceneContext | null | undefined,
  model: ProtocolModel | null | undefined,
  label: string,
  payload: SceneObjectDragPayload | null
): SnapDraggedObject | null {
  if (payload?.item) return snapDraggedObjectForLibraryItem(payload.item);
  if (!label) return null;
  const labware = (context?.model.labware || model?.labware || []).find((item) => labwareLabelMatches(item.label, label));
  if (!labware) return null;
  return {
    kind: sceneObjectStyleFor(labware).kind,
    componentGuid: normalizeGuidText(labware.transform?.componentGuid || ""),
    hardwareProfile: labware.hardwareProfile,
    roleText: sceneObjectText(labware)
  };
}

function snapDraggedObjectForLibraryItem(item: ObjectLibraryItem): SnapDraggedObject {
  const text = `${item.name} ${item.profile} ${item.componentRole || ""} ${item.tags.join(" ")}`.toLowerCase();
  return {
    kind: sceneObjectKindForObjectLibraryItem(item, text),
    hardwareProfile: item.profile,
    roleText: text
  };
}

function sceneObjectKindForObjectLibraryItem(item: ObjectLibraryItem, text: string): SceneObjectKind {
  if (item.profile === "tip-box" || text.includes("tip")) return "tip-box";
  if (item.profile === "carrier" || text.includes("carrier") || text.includes("runner") || text.includes("segment")) return "carrier";
  if (item.profile === "nest" || text.includes("nest")) return "nest";
  if (item.profile === "adapter" || text.includes("adapter")) return "adapter";
  if (item.profile === "device" || text.includes("device")) return "device";
  if (item.profile === "structural" || text.includes("base rail") || text.includes("deck")) return "structural";
  if (item.profile === "tube-holder" || text.includes("tube")) return "tube-rack";
  if (item.profile === "waste") return "waste";
  return item.rows || item.cols || text.includes("plate") || text.includes("well") ? "labware" : "unknown";
}

function snapTargetCompatibleWithDraggedObject(target: SceneSnapTarget, dragged: SnapDraggedObject | null): boolean {
  if (!dragged) return true;
  if (dragged.kind === "device") return targetCompatibleByOfficialConnector(target, dragged);
  if (targetCompatibleByOfficialConnector(target, dragged)) return true;
  if (target.compatibleComponentGuids?.length && dragged.componentGuid) return false;

  const siteText = `${target.siteLocationGroupName || ""} ${target.label} ${target.parentLabel || ""}`.toLowerCase();
  const roleText = dragged.roleText.toLowerCase();
  const parentKind = target.parentKind || "unknown";

  if (dragged.kind === "nest" || dragged.kind === "adapter") {
    return parentKind === "carrier" || parentKind === "structural" || siteText.includes("carrier") || siteText.includes("adapter") || siteText.includes("nest");
  }
  if (dragged.kind === "carrier" || roleText.includes("segment")) {
    return parentKind === "structural" || siteText.includes("segment") || siteText.includes("base") || siteText.includes("rail") || siteText.includes("grid");
  }
  if (dragged.kind === "labware" || dragged.kind === "tube-rack" || dragged.kind === "tip-box" || dragged.hardwareProfile === "plate") {
    if (dragged.kind === "tip-box") return parentKind === "carrier" || siteText.includes("tip") || siteText.includes("diti");
    return parentKind === "nest" || parentKind === "adapter" || siteText.includes("labware") || siteText.includes("nest") || siteText.includes("plate");
  }
  if (dragged.kind === "waste") return siteText.includes("waste") || siteText.includes("wash") || parentKind === "carrier";
  return parentKind !== "device";
}

function targetCompatibleByOfficialConnector(target: SceneSnapTarget, dragged: SnapDraggedObject): boolean {
  const componentGuid = normalizeGuidText(dragged.componentGuid || "");
  return Boolean(componentGuid && target.compatibleComponentGuids?.some((guid) => normalizeGuidText(guid) === componentGuid));
}

function deckSlotSnapTargets(model: ProtocolModel | null | undefined, excludeLabel = "", dragged: SnapDraggedObject | null = null): SceneSnapTarget[] {
  if (dragged?.kind === "device" || dragged?.kind === "structural") return [];
  const occupied = occupiedSnapSlotsFor(model, excludeLabel);
  const targets: SceneSnapTarget[] = [];
  for (let slotIndex = 0; slotIndex < SNAP_SLOT_COUNT; slotIndex += 1) {
    targets.push({
      key: `deck:${slotIndex}`,
      label: `Deck slot ${slotIndex + 1}`,
      position: slotPositionFromIndex(slotIndex),
      rotationY: 0,
      slotIndex,
      source: "deck-slot",
      status: occupied.has(slotIndex) ? "occupied" : "compatible",
      size: { width: SLOT_WIDTH * 0.76, depth: SLOT_DEPTH * 0.7 }
    });
  }
  return targets;
}

function occupiedSnapSlotsFor(model: ProtocolModel | null | undefined, excludeLabel = ""): Set<number> {
  const occupied = new Set<number>();
  model?.labware.forEach((labware) => {
    if (excludeLabel && labwareLabelMatches(labware.label, excludeLabel)) return;
    const slotIndex = labware.position ? labware.position - 1 : labware.slotIndex;
    if (Number.isFinite(slotIndex) && slotIndex >= 0 && slotIndex < SNAP_SLOT_COUNT) occupied.add(Math.floor(slotIndex));
  });
  return occupied;
}

function snapTargetContainingPoint(position: THREE.Vector3, targets: SceneSnapTarget[]): SceneSnapTarget | null {
  return (
    targets.find((target) => {
      if (target.status !== "compatible") return false;
      const local = position.clone().sub(target.position).applyAxisAngle(new THREE.Vector3(0, 1, 0), -target.rotationY);
      return Math.abs(local.x) <= target.size.width / 2 && Math.abs(local.z) <= target.size.depth / 2;
    }) || null
  );
}

function itemAtClampedIndex<T>(items: T[], activeIndex: number): T | undefined {
  if (!items.length) return undefined;
  const index = clamp(Math.floor(Number.isFinite(activeIndex) ? activeIndex : 0), 0, items.length - 1);
  return items[index];
}

function sceneGeometryFor(labware: LabwareModel[]): SceneGeometryContext {
  const transformed = labware.filter((item) => item.transform);
  if (!transformed.length) {
    return {
      hasFluentGeometry: false,
      scale: 1,
      centerX: 0,
      centerY: 0,
      minZ: 0
    };
  }

  const operationalTransformed = transformed.filter((item) => shouldContributeToSceneBounds(item));
  const boundsItems = operationalTransformed.length ? operationalTransformed : transformed;
  const points = boundsItems.flatMap((item) => fluentFootprintCorners(item));
  if (!points.length) {
    return {
      hasFluentGeometry: false,
      scale: 1,
      centerX: 0,
      centerY: 0,
      minZ: 0
    };
  }

  const xs = points.map((point) => point.x).filter(Number.isFinite);
  const ys = points.map((point) => point.y).filter(Number.isFinite);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  const scale = Math.min((DECK_WIDTH - 4) / spanX, (DECK_DEPTH - 4) / spanY, 0.08);

  return {
    hasFluentGeometry: true,
    scale,
    centerX: (minX + maxX) / 2,
    centerY: (minY + maxY) / 2,
    // Anchor the vertical origin to the Fluent deck surface (z = 0 by convention),
    // NOT the global minimum z. A few fixtures are mounted well below the surface
    // (e.g. the floor-standing Resolvex A200 at z ≈ -780, the frame walls at
    // z ≈ -137); using the global min would lift the entire worktable off those
    // outliers and leave all labware floating ~13 units above the rendered deck.
    // Surface labware (z ≈ 0) maps to y ≈ 0, while floor-standing/external
    // devices keep their negative Z and render below the deck instead of being
    // lifted onto the work surface.
    minZ: 0
  };
}

function sceneLabwareFor(labware: LabwareModel[], geometry: SceneGeometryContext, showDebugGeometry: boolean): LabwareModel[] {
  return labware.filter((item) => shouldRenderLabwareInScene(item, geometry, showDebugGeometry));
}

function shouldRenderLabwareInScene(labware: LabwareModel, geometry: SceneGeometryContext, showDebugGeometry: boolean): boolean {
  if (!geometry.hasFluentGeometry) return true;
  if (!labware.transform && labware.role === "protocol_target") return false;
  if (!showDebugGeometry && isNormalModeContextObject(labware)) return false;
  return true;
}

function shouldContributeToSceneBounds(labware: LabwareModel): boolean {
  const style = sceneObjectStyleFor(labware);
  // Frame the camera / center the deck on the functional worktable (carriers,
  // plates, tubes, nests). The big enclosure walls and bulky instruments are
  // rendered, but excluding them from the bounds keeps the view centered on the
  // working area instead of being pulled off-center by an off-deck wall or a
  // rear-mounted reader (which also made the side walls look asymmetric).
  if (style.kind === "structural") return false;
  if (style.kind === "device" && !style.liquidCapable && !labware.wells.length) return false;
  return true;
}

function isNormalModeContextObject(labware: LabwareModel): boolean {
  const style = sceneObjectStyleFor(labware);
  // Real on-deck hardware that ships a native Fluent mesh — the cabinet frame /
  // walls (structural) and instruments like the Microhawk camera or Infinite
  // M200 reader (device) — should render so the scene matches FluentControl.
  // Only abstract / mesh-less structural and device parts stay hidden as context
  // to keep the deck readable.
  if (style.kind === "device" && !style.liquidCapable && !labware.wells.length) {
    // A device with a real worktable position (transform) renders either with its
    // mesh or, when we have no mesh for it (e.g. TubeEyeX_Rear), as a clean labeled
    // translucent placeholder — honestly "incomplete" rather than silently absent.
    // Only purely abstract devices with no placement stay hidden as context.
    return !labware.transform;
  }
  if (style.kind === "structural") {
    return !isNativeFluentModelAsset(labware, modelAssetPathForLabware(labware));
  }
  return false;
}

function fluentFootprintCorners(labware: LabwareModel): Vector3Model[] {
  const transform = labware.transform;
  if (!transform) return [];
  const dimension = fluentDimensionFor(labware);
  const center = fluentBodyCenter(labware);
  const halfX = dimension.x / 2;
  const halfY = dimension.y / 2;
  return [
    { x: -halfX, y: -halfY, z: 0 },
    { x: halfX, y: -halfY, z: 0 },
    { x: halfX, y: halfY, z: 0 },
    { x: -halfX, y: halfY, z: 0 }
  ].map((corner) => addFluentVectors(center, rotateFluentVector(transform.orientation, corner)));
}

function labwarePlacement(labware: LabwareModel, geometry: SceneGeometryContext): LabwarePlacement {
  const size = labwareSize(labware, geometry);
  if (!geometry.hasFluentGeometry || !labware.transform) {
    return {
      position: slotPosition(labware),
      rotationY: 0,
      size
    };
  }

  return {
    position: fluentToThree(fluentBodyCenter(labware), geometry),
    rotationY: yawFromFluentOrientation(labware.transform.orientation),
    size
  };
}

function addAnchoredProtocolTargets(
  runtime: RuntimeScene,
  model: ProtocolModel,
  geometry: SceneGeometryContext,
  snapshot: SnapshotModel | undefined,
  activeCommand: CommandModel | undefined,
  focusTarget: SceneFocusTarget | null,
  labwarePlacements: Map<string, LabwarePlacement>,
  labwareOverrides: LabwareSceneOverrides,
  activeTargetHasPhysicalMatch: boolean,
  focusTargetHasPhysicalMatch: boolean
): void {
  if (!geometry.hasFluentGeometry) return;

  const candidates = model.labware.filter((labware) => labware.role === "protocol_target" && !labware.transform);
  const remaining = new Set(candidates.map((labware) => labware.label));

  for (let pass = 0; pass < candidates.length && remaining.size; pass += 1) {
    let placedThisPass = false;

    candidates.forEach((labware) => {
      if (!remaining.has(labware.label)) return;
      const transfer = snapshotLabwareTransfer(snapshot, labware);
      if (transfer?.moveToBase) {
        remaining.delete(labware.label);
        labwareOverrides.set(labware.label, labwareWithTransferLocation(labware, transfer));
        return;
      }

      const target = transfer ? labwareWithTransferLocation(labware, transfer) : labware;
      if (!shouldAnchorProtocolTarget(target)) return;
      if (labwarePlacements.has(labware.label)) {
        remaining.delete(labware.label);
        return;
      }
      if (!transfer && hasPlacedTransformedLabelMatch(labware, model.labware, labwarePlacements)) {
        remaining.delete(labware.label);
        return;
      }

      const anchor = (transfer && transferAnchor(transfer, model.labware, labwarePlacements, labwareOverrides)) || protocolTargetAnchor(target, model.labware, labwarePlacements);
      if (!anchor) return;

      const sceneLabware = labwareWithAnchoredProtocolGeometry(target, model.labware, anchor);
      if (sceneLabware !== labware) labwareOverrides.set(labware.label, sceneLabware);
      const placement = anchoredProtocolTargetPlacement(sceneLabware, geometry, anchor);
      applyInteractivePlacementOverride(runtime, sceneLabware, placement);
      labwarePlacements.set(labware.label, placement);
      addLabware(runtime, runtime.root, sceneLabware, placement, geometry, snapshot, activeCommand, focusTarget, activeTargetHasPhysicalMatch, focusTargetHasPhysicalMatch);
      remaining.delete(labware.label);
      placedThisPass = true;
    });

    if (!placedThisPass) break;
  }
}

function shouldAnchorProtocolTarget(labware: LabwareModel): boolean {
  return labware.role === "protocol_target" && !labware.transform && Boolean(labware.location);
}

function snapshotLabwareTransfer(snapshot: SnapshotModel | undefined, labware: LabwareModel): LabwareTransferModel | undefined {
  if (!snapshot?.labwareLocations) return undefined;
  return (
    snapshot.labwareLocations[labware.label] ||
    Object.entries(snapshot.labwareLocations).find(([label]) => labwareLabelMatches(label, labware.label))?.[1]
  );
}

function labwareWithTransferLocation(labware: LabwareModel, transfer: LabwareTransferModel): LabwareModel {
  const position = numericTransferSite(transfer.site);
  return {
    ...labware,
    location: transfer.location,
    position,
    positionExpression: transfer.site || transfer.location || labware.positionExpression
  };
}

function numericTransferSite(site: string): number | null {
  const value = Number(site);
  return Number.isFinite(value) ? value : null;
}

function transferAnchor(
  transfer: LabwareTransferModel,
  labwareItems: LabwareModel[],
  labwarePlacements: Map<string, LabwarePlacement>,
  labwareOverrides: LabwareSceneOverrides
): { labware: LabwareModel; placement: LabwarePlacement } | null {
  const anchorLabel = transfer.anchorLabware || "";
  if (!anchorLabel) return null;
  const placement = placementForLabwareLabel(labwarePlacements, anchorLabel);
  if (!placement) return null;
  const labware = labwareOverrideForLabel(labwareOverrides, anchorLabel) || labwareForLabel(labwareItems, anchorLabel);
  return labware ? { labware, placement } : null;
}

function labwareForLabel(labwareItems: LabwareModel[], label: string): LabwareModel | null {
  return labwareItems.find((labware) => labwareLabelMatches(labware.label, label)) || null;
}

function labwareOverrideForLabel(overrides: LabwareSceneOverrides, label: string): LabwareModel | null {
  return overrides.get(label) || Array.from(overrides.entries()).find(([candidate]) => labwareLabelMatches(candidate, label))?.[1] || null;
}

function hasPlacedTransformedLabelMatch(
  target: LabwareModel,
  labwareItems: LabwareModel[],
  labwarePlacements: Map<string, LabwarePlacement>
): boolean {
  return labwareItems.some((labware) => labware.transform && labwarePlacements.has(labware.label) && labwareLabelMatches(labware.label, target.label));
}

function hasPhysicalTargetMatch(labwareItems: LabwareModel[], targetLabel: string): boolean {
  return Boolean(targetLabel && labwareItems.some((labware) => labware.transform && labwareLabelMatches(labware.label, targetLabel)));
}

function protocolTargetAnchor(
  target: LabwareModel,
  labwareItems: LabwareModel[],
  labwarePlacements: Map<string, LabwarePlacement>
): { labware: LabwareModel; placement: LabwarePlacement } | null {
  const terms = protocolTargetAnchorTerms(target);
  const hints = protocolAnchorHints(target);
  if (!terms.length && !hints.exactLabelScores.size && !hints.positionScores.size) return null;

  const scored = labwareItems
    .filter((labware) => labware.transform && labwarePlacements.has(labware.label))
    .map((labware) => ({
      labware,
      placement: labwarePlacements.get(labware.label) as LabwarePlacement,
      score: protocolAnchorScore(target, labware, terms, hints)
    }))
    .filter((candidate) => candidate.score > 0)
    .sort((a, b) => b.score - a.score || a.labware.label.localeCompare(b.labware.label));

  return scored[0] || null;
}

function protocolTargetAnchorTerms(target: LabwareModel): string[] {
  // Derive match terms from ZEIA/worktable fields on the target itself.
  // Never invent Nest61mm / A200 / Falcon / tube-runner search tokens.
  const stop = new Set([
    "the",
    "and",
    "for",
    "pos",
    "position",
    "site",
    "index",
    "null",
    "undefined",
    "true",
    "false",
    "getcoversitename",
    "getcoversiteindex"
  ]);
  const terms: string[] = [];
  const seen = new Set<string>();
  const push = (raw: string, minLen = 3) => {
    const text = normalizeAnchorText(raw);
    if (!text || text.length < minLen || stop.has(text) || seen.has(text)) return;
    seen.add(text);
    terms.push(text);
  };

  // Prefer exact location / catalog / label from the imported deck.
  push(target.location || "", 2);
  push(stripFluentInstanceSuffixLocal(target.catalogName || ""), 2);
  push(stripFluentInstanceSuffixLocal(target.label || ""), 2);

  for (const source of [target.location, target.catalogName, target.label, target.positionExpression]) {
    const text = normalizeAnchorText(source || "");
    if (!text) continue;
    for (const token of text.split(" ")) {
      if (/^\d+$/.test(token) || stop.has(token)) continue;
      push(token, 3);
    }
  }

  if (target.hardwareProfile) {
    push(target.hardwareProfile.replace(/[-_]+/g, " "), 3);
  }
  return terms.slice(0, 16);
}

function stripFluentInstanceSuffixLocal(value: string): string {
  return value.replace(/\[[^\]]+\]\s*$/, "").trim();
}

function protocolAnchorScore(target: LabwareModel, candidate: LabwareModel, terms: string[], hints: ProtocolAnchorHints): number {
  const candidateText = normalizeAnchorText(
    `${candidate.label} ${candidate.catalogName} ${candidate.role} ${candidate.location} ${candidate.transform?.path.join(" ") || ""} ${candidate.transform?.parentLabels.join(" ") || ""}`
  );
  let score = 0;
  const candidateLabel = normalizeAnchorText(candidate.label);
  hints.exactLabelScores.forEach((hintScore, hintLabel) => {
    if (candidateLabel === hintLabel) score += hintScore;
    else if (candidateText.includes(hintLabel)) score += Math.max(0, hintScore - 25);
  });
  const candidatePosition = trailingBracketPosition(candidate.label);
  if (candidatePosition !== null) score += hints.positionScores.get(candidatePosition) || 0;
  terms.forEach((term, index) => {
    if (candidateText.includes(normalizeAnchorText(term))) score += 60 - index * 8;
  });
  // Soft role affinity only — no Nest61mm / Falcon / A200 / tube-runner invents.
  const candidateKind = sceneObjectStyleFor(candidate).kind;
  if (target.hardwareProfile === "24-filter-plate" && candidateKind === "nest") score += 30;
  if (target.hardwareProfile === "24-dwp" && candidateKind === "nest") score += 24;
  if (target.hardwareProfile === "tube-holder" && (candidateKind === "tube-rack" || candidate.wells.length > 1)) score += 36;
  if (candidateKind === "nest") score += 12;
  if (candidate.wells.length) score -= 10;
  return score;
}

function protocolAnchorHints(target: LabwareModel): ProtocolAnchorHints {
  const exactLabelScores = new Map<string, number>();
  const positionScores = new Map<number, number>();
  const addExactLabel = (label: string, score: number) => {
    const key = normalizeAnchorText(label);
    exactLabelScores.set(key, Math.max(exactLabelScores.get(key) || 0, score));
  };
  const addPosition = (position: number | null, score: number) => {
    if (position === null || !Number.isFinite(position) || position <= 0) return;
    const rounded = Math.round(position);
    positionScores.set(rounded, Math.max(positionScores.get(rounded) || 0, score));
  };

  if (target.position !== null) addPosition(target.position, 110);

  const expressionValue = positionValueFromExpression(target.positionExpression || "");
  const positionValues: Array<{ value: number; score: number }> = [];
  if (expressionValue !== null) {
    positionValues.push({ value: expressionValue, score: 125 });
    if (positionExpressionMayBeZeroBased(target.positionExpression || "")) {
      positionValues.push({ value: expressionValue + 1, score: 150 });
    }
  }
  positionValues.forEach(({ value, score }) => addPosition(value, score));

  // Prefer exact catalog/label from the target itself (ZEIA / worktable), never invent
  // CapHolder_long_44mm / Falcon runners / 61mm Nest / Resolvex A200 / 7mm Nest labels.
  const bareCatalog = (target.catalogName || "").replace(/\[[^\]]+\]\s*$/, "").trim();
  const bareLabel = (target.label || "").replace(/\[[^\]]+\]\s*$/, "").trim();
  if (bareCatalog) {
    positionScores.forEach((score, position) => addExactLabel(`${bareCatalog}[${formatFluentPosition(position)}]`, score + 160));
    addExactLabel(target.catalogName, 200);
  }
  if (bareLabel && bareLabel !== bareCatalog) {
    positionScores.forEach((score, position) => addExactLabel(`${bareLabel}[${formatFluentPosition(position)}]`, score + 140));
    addExactLabel(target.label, 180);
  }

  return { exactLabelScores, positionScores };
}

function positionValueFromExpression(expression: string): number | null {
  const trimmed = expression.trim();
  if (!trimmed) return null;
  const numeric = Number(trimmed);
  if (Number.isFinite(numeric)) return numeric;
  const substituted = trimmed.replace(/[A-Za-z_][A-Za-z0-9_]*/g, "1");
  if (!/^[\d+\-\s.]+$/.test(substituted)) return null;
  const tokens = substituted.match(/[+-]?\s*\d+(?:\.\d+)?/g);
  if (!tokens?.length) return null;
  const value = tokens.reduce((sum, token) => sum + Number(token.replace(/\s+/g, "")), 0);
  return Number.isFinite(value) ? value : null;
}

function positionExpressionMayBeZeroBased(expression: string): boolean {
  return /[A-Za-z_][A-Za-z0-9_]*|[+\-]/.test(expression);
}

function formatFluentPosition(position: number): string {
  return String(Math.round(position)).padStart(3, "0");
}

function trailingBracketPosition(value: string): number | null {
  const match = value.match(/\[(\d+)\]\s*$/);
  if (!match) return null;
  const position = Number(match[1]);
  return Number.isFinite(position) ? position : null;
}

function normalizeAnchorText(value: string): string {
  return value.toLowerCase().replace(/[_\-\s]+/g, " ").replace(/[^a-z0-9 ]+/g, "").replace(/\s+/g, " ").trim();
}

function anchoredProtocolTargetPlacement(
  labware: LabwareModel,
  geometry: SceneGeometryContext,
  anchor: { labware: LabwareModel; placement: LabwarePlacement }
): LabwarePlacement {
  const size = labwareSize(labware, geometry, true);
  const position = anchor.placement.position.clone();
  const siteOffset = anchoredProtocolTargetSiteOffset(labware, geometry, anchor.labware, anchor.placement);
  if (siteOffset) {
    siteOffset.applyAxisAngle(new THREE.Vector3(0, 1, 0), anchor.placement.rotationY);
    position.add(siteOffset);
  }
  position.y = anchor.placement.position.y + Math.max(0.08, anchor.placement.size.height) + 0.02;
  return {
    position,
    rotationY: anchor.placement.rotationY,
    size
  };
}

function anchoredProtocolTargetSiteOffset(
  target: LabwareModel,
  geometry: SceneGeometryContext,
  anchorLabware: LabwareModel,
  anchorPlacement: LabwarePlacement
): THREE.Vector3 | null {
  if (target.wells.length !== 1 || anchorLabware.wells.length <= 1) return null;
  if (!isTubeLabware(target) && !sceneObjectText(target).includes("sample source tube")) return null;

  const wellIndex = protocolTargetWellIndex(target, anchorLabware.wells.length);
  if (wellIndex === null) return null;
  const anchorWell = anchorLabware.wells[wellIndex];
  if (!anchorWell) return null;

  const layout = wellLayoutFor(anchorLabware, anchorPlacement.size);
  const scenePosition = sceneWellPositionForPlacement(anchorWell, anchorLabware, anchorPlacement, geometry, layout);
  return scenePosition.sub(anchorPlacement.position).applyAxisAngle(new THREE.Vector3(0, 1, 0), -anchorPlacement.rotationY);
}

function protocolTargetWellIndex(target: LabwareModel, siteCount: number): number | null {
  const candidates: number[] = [];
  const addCandidate = (value: number | null) => {
    if (value === null || !Number.isFinite(value)) return;
    const index = Math.round(value);
    if (index >= 0 && index < siteCount && !candidates.includes(index)) candidates.push(index);
  };

  if (target.position !== null) addCandidate(target.position - 1);
  const expressionValue = positionValueFromExpression(target.positionExpression || "");
  if (expressionValue !== null) {
    addCandidate(expressionValue - 1);
    if (positionExpressionMayBeZeroBased(target.positionExpression || "")) addCandidate(expressionValue);
  }

  return candidates[0] ?? null;
}

function labwareWithAnchoredProtocolGeometry(
  target: LabwareModel,
  labwareItems: LabwareModel[],
  anchor: { labware: LabwareModel }
): LabwareModel {
  const withWells = labwareWithBorrowedWellGeometry(target, labwareItems, anchor);
  if (withWells.wells.length) return withWells;
  return labwareWithoutModelAsset(withWells);
}

function labwareWithoutModelAsset(labware: LabwareModel): LabwareModel {
  return {
    ...labware,
    modelAssetPath: "",
    modelAssetFormat: undefined,
    meshGuid: "",
    meshName: "",
    meshSourcePath: ""
  };
}

function labwareWithBorrowedWellGeometry(
  target: LabwareModel,
  labwareItems: LabwareModel[],
  anchor: { labware: LabwareModel }
): LabwareModel {
  const donor = fluentWellGeometryDonorFor(target, labwareItems, anchor.labware);
  if (!donor) return target;
  const donorWellsById = new Map(donor.wells.map((well) => [well.id, well]));
  const wells = target.wells.map((well, index) => {
    const donorWell = donorWellsById.get(well.id) || donor.wells[index];
    if (!donorWell?.positionMm) return well;
    return {
      ...well,
      positionMm: donorWell.positionMm,
      positionSource: donorWell.positionSource,
      diameterMm: donorWell.diameterMm || well.diameterMm,
      depthMm: donorWell.depthMm || well.depthMm
    };
  });

  return {
    ...target,
    geometryName: donor.geometryName || target.geometryName,
    geometrySource: donor.geometrySource,
    rows: donor.rows,
    cols: donor.cols,
    wellShape: donor.wellShape,
    physicalWidthMm: donor.physicalWidthMm,
    physicalDepthMm: donor.physicalDepthMm,
    physicalHeightMm: donor.physicalHeightMm,
    pitchXMm: donor.pitchXMm,
    pitchYMm: donor.pitchYMm,
    wellDiameterMm: donor.wellDiameterMm,
    wellDepthMm: donor.wellDepthMm,
    wells
  };
}

function fluentWellGeometryDonorFor(target: LabwareModel, labwareItems: LabwareModel[], anchorLabware: LabwareModel): LabwareModel | null {
  if (!target.wells.length || target.wells.some((well) => well.positionMm)) return null;
  const candidates = [anchorLabware, ...labwareItems]
    .filter((candidate, index, array) => array.findIndex((item) => item.label === candidate.label) === index)
    .filter((candidate) => candidate.wells.length === target.wells.length && hasFluentWellPositions(candidate));

  const scored = candidates
    .map((candidate) => ({ candidate, score: wellGeometryDonorScore(target, candidate, candidate === anchorLabware) }))
    .filter((item) => item.score >= 120)
    .sort((a, b) => b.score - a.score || a.candidate.label.localeCompare(b.candidate.label));

  return scored[0]?.candidate || null;
}

function hasFluentWellPositions(labware: LabwareModel): boolean {
  return Boolean(labware.wells.length && labware.wells.every((well) => well.positionMm && well.positionSource !== "catalog"));
}

function wellGeometryDonorScore(target: LabwareModel, candidate: LabwareModel, isAnchor: boolean): number {
  const targetText = normalizeAnchorText(`${target.label} ${target.catalogName} ${target.hardwareProfile}`);
  const candidateText = normalizeAnchorText(`${candidate.label} ${candidate.catalogName} ${candidate.hardwareProfile}`);
  let score = 0;
  if (isAnchor) score += 120;
  if (labwareLabelMatches(candidate.label, target.label)) score += 160;
  if (normalizeLabwareLabel(candidate.catalogName) === normalizeLabwareLabel(target.catalogName)) score += 90;
  if (candidate.hardwareProfile === target.hardwareProfile) score += 70;
  if (candidate.rows === target.rows && candidate.cols === target.cols) score += 45;
  if (candidate.transform) score += 35;
  if (candidateText.includes(targetText) || targetText.includes(candidateText)) score += 30;
  return score;
}

function fluentBodyCenter(labware: LabwareModel): Vector3Model {
  const transform = labware.transform;
  if (!transform) return { x: 0, y: 0, z: 0 };
  const dimension = fluentDimensionFor(labware);
  return addFluentVectors(
    transform.positionMm,
    rotateFluentVector(transform.orientation, { x: dimension.x / 2, y: dimension.y / 2, z: 0 })
  );
}

function fluentDimensionFor(labware: LabwareModel): Vector3Model {
  return (
    labware.transform?.dimensionMm || {
      x: labware.physicalWidthMm,
      y: labware.physicalDepthMm,
      z: labware.physicalHeightMm
    }
  );
}

function fluentToThree(point: Vector3Model, geometry: SceneGeometryContext): THREE.Vector3 {
  return new THREE.Vector3(
    (point.x - geometry.centerX) * geometry.scale,
    (point.z - geometry.minZ) * geometry.scale,
    -(point.y - geometry.centerY) * geometry.scale
  );
}

function rotateFluentVector(matrix: Matrix3Model, vector: Vector3Model): Vector3Model {
  return {
    x: matrix[0][0] * vector.x + matrix[0][1] * vector.y + matrix[0][2] * vector.z,
    y: matrix[1][0] * vector.x + matrix[1][1] * vector.y + matrix[1][2] * vector.z,
    z: matrix[2][0] * vector.x + matrix[2][1] * vector.y + matrix[2][2] * vector.z
  };
}

function addFluentVectors(a: Vector3Model, b: Vector3Model): Vector3Model {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

function yawFromFluentOrientation(matrix: Matrix3Model): number {
  const localXAxisInFluent = { x: matrix[0][0], y: matrix[1][0] };
  return Math.atan2(localXAxisInFluent.y, localXAxisInFluent.x);
}

function addLighting(scene: THREE.Scene): THREE.Light[] {
  const hemi = new THREE.HemisphereLight("#eef3f6", "#41474c", 1.35);
  scene.add(hemi);

  const ambient = new THREE.AmbientLight("#ffffff", 0.35);
  scene.add(ambient);

  const key = new THREE.DirectionalLight("#ffffff", 2.1);
  key.position.set(-8, 18, 12);
  key.castShadow = true;
  key.shadow.camera.near = 1;
  key.shadow.camera.far = 60;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.bias = -0.0004;
  scene.add(key);

  const fill = new THREE.DirectionalLight("#cfe6ff", 0.85);
  fill.position.set(12, 10, -18);
  scene.add(fill);

  const rim = new THREE.DirectionalLight("#aab6c2", 0.5);
  rim.position.set(0, 8, -22);
  scene.add(rim);

  return [hemi, ambient, key, fill, rim];
}

function addDeck(root: THREE.Group, hasFluentGeometry = false): void {
  if (!hasFluentGeometry) {
    const deck = new THREE.Mesh(
      new THREE.BoxGeometry(DECK_WIDTH, 0.4, DECK_DEPTH),
      new THREE.MeshStandardMaterial({ color: "#e7ebec", roughness: 0.66, metalness: 0.05 })
    );
    deck.receiveShadow = true;
    deck.position.y = -0.24;
    root.add(deck);
  }

  const railMaterial = new THREE.MeshStandardMaterial({ color: "#8b9694", roughness: 0.55, metalness: 0.08 });
  const backRail = new THREE.Mesh(new THREE.BoxGeometry(DECK_WIDTH + 1.2, 0.22, 0.24), railMaterial);
  backRail.position.set(0, 0.25, -DECK_DEPTH / 2 - 0.45);
  root.add(backRail);

  const frontRail = backRail.clone();
  frontRail.position.z = DECK_DEPTH / 2 + 0.45;
  root.add(frontRail);

  if (!hasFluentGeometry) addInstrumentCabinet(root);
}

function addInstrumentCabinet(root: THREE.Group): void {
  const deckTopY = -0.04;
  const cabinetWidth = DECK_WIDTH * 0.32;
  const cabinetDepth = DECK_DEPTH + 1.6;
  const cabinetHeight = 12;
  const cabinetTopY = deckTopY - 0.46;

  const bodyMaterial = new THREE.MeshStandardMaterial({ color: "#1f6f93", roughness: 0.5, metalness: 0.12 });
  const body = new THREE.Mesh(new THREE.BoxGeometry(cabinetWidth, cabinetHeight, cabinetDepth), bodyMaterial);
  body.position.set(0, cabinetTopY - cabinetHeight / 2, 0);
  body.castShadow = true;
  body.receiveShadow = true;
  root.add(body);

  const trimMaterial = new THREE.MeshStandardMaterial({ color: "#15596f", roughness: 0.55, metalness: 0.1 });
  const trim = new THREE.Mesh(new THREE.BoxGeometry(cabinetWidth + 0.5, 0.7, cabinetDepth + 0.5), trimMaterial);
  trim.position.set(0, cabinetTopY - 0.35, 0);
  root.add(trim);

  const accessMaterial = new THREE.MeshStandardMaterial({ color: "#0e3f52", roughness: 0.6, metalness: 0.08 });
  const access = new THREE.Mesh(
    new THREE.BoxGeometry(cabinetWidth * 0.62, cabinetHeight * 0.34, 0.4),
    accessMaterial
  );
  access.position.set(0, cabinetTopY - cabinetHeight * 0.32, cabinetDepth / 2 + 0.02);
  root.add(access);
}

function getContactShadowTexture(): THREE.Texture {
  if (contactShadowTexture) return contactShadowTexture;
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  const context = canvas.getContext("2d");
  if (context) {
    const gradient = context.createRadialGradient(64, 64, 6, 64, 64, 62);
    gradient.addColorStop(0, "rgba(0,0,0,0.46)");
    gradient.addColorStop(0.55, "rgba(0,0,0,0.22)");
    gradient.addColorStop(1, "rgba(0,0,0,0)");
    context.fillStyle = gradient;
    context.fillRect(0, 0, 128, 128);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.userData.persistent = true;
  contactShadowTexture = texture;
  return texture;
}

// Lays a soft radial contact shadow on the deck beneath a labware footprint so
// items read as grounded. Skips small/flat items (tubes, thin base grids) to
// avoid muddying the deck with overlapping shadow blobs.
function addContactShadow(root: THREE.Group, placement: LabwarePlacement, bodyHeight: number): void {
  const { width, depth } = placement.size;
  if (Math.min(width, depth) < 1.1 || bodyHeight < 0.3) return;

  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(width * 1.2, depth * 1.2),
    new THREE.MeshBasicMaterial({
      map: getContactShadowTexture(),
      transparent: true,
      opacity: 0.5,
      depthWrite: false,
      toneMapped: false,
    })
  );
  plane.quaternion.setFromEuler(new THREE.Euler(-Math.PI / 2, 0, 0));
  plane.rotateZ(placement.rotationY);
  // Anchor the shadow just above the object's base (the group origin sits at the
  // base). Deck labware has base y≈0, so this keeps the usual deck-level blob,
  // but parts mounted on the elevated rear shelf (Microhawk, Infinite M200, Vial
  // Gripper at y≈15.6) get their shadow under them on the shelf instead of a
  // detached blob on the deck far below.
  plane.position.set(placement.position.x, placement.position.y + CONTACT_SHADOW_Y, placement.position.z);
  plane.renderOrder = 1;
  plane.raycast = () => undefined;
  root.add(plane);
}

function addDeckRulers(root: THREE.Group): void {
  const material = new THREE.MeshBasicMaterial({ color: "#a8b0ad", transparent: true, opacity: 0.62 });
  for (let i = 0; i < 18; i += 1) {
    const x = -DECK_WIDTH / 2 + 2 + (i % SLOT_COLUMNS) * SLOT_WIDTH;
    const z = -DECK_DEPTH / 2 + 2.4 + Math.floor(i / SLOT_COLUMNS) * SLOT_DEPTH;
    const marker = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.03, SLOT_DEPTH - 0.7), material);
    marker.position.set(x - SLOT_WIDTH / 2, 0.02, z);
    root.add(marker);
  }
}

const DECK_GRID_PITCH_MM = 25;
function addDeckGridRuler(root: THREE.Group, geometry: SceneGeometryContext, labware: LabwareModel[]): void {
  if (!geometry.hasFluentGeometry) return;
  const corners = labware
    .filter((item) => item.transform && shouldContributeToSceneBounds(item))
    .flatMap((item) => fluentFootprintCorners(item));
  const xs = corners.map((corner) => corner.x).filter(Number.isFinite);
  const ys = corners.map((corner) => corner.y).filter(Number.isFinite);
  if (xs.length < 2 || ys.length < 2) return;

  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const spanXmm = maxX - minX;
  if (spanXmm <= 0) return;

  const gridCount = Math.min(96, Math.max(12, Math.round(spanXmm / DECK_GRID_PITCH_MM)));
  const cell = 56;
  const canvas = document.createElement("canvas");
  canvas.width = gridCount * cell;
  canvas.height = 112;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "rgba(237,241,242,0.97)";
  ctx.fillRect(0, 20, canvas.width, 72);
  ctx.fillStyle = "#39474b";
  ctx.font = "bold 44px Arial, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.strokeStyle = "rgba(120,134,132,0.85)";
  ctx.lineWidth = 2;
  for (let index = 0; index < gridCount; index += 1) {
    const centerPx = (index + 0.5) * cell;
    ctx.fillText(String(index + 1), centerPx, 58);
    const edgePx = index * cell;
    ctx.beginPath();
    ctx.moveTo(edgePx, 20);
    ctx.lineTo(edgePx, 92);
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.moveTo(canvas.width - 1, 20);
  ctx.lineTo(canvas.width - 1, 92);
  ctx.stroke();

  const texture = new THREE.CanvasTexture(canvas);
  texture.anisotropy = 8;
  texture.needsUpdate = true;
  const material = new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false });

  const widthUnits = spanXmm * geometry.scale;
  const depthUnits = Math.max(0.85, (widthUnits / gridCount) * (canvas.height / cell));
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(widthUnits, depthUnits), material);
  plane.rotation.x = -Math.PI / 2;
  const centerXUnits = ((minX + maxX) / 2 - geometry.centerX) * geometry.scale;
  const frontZUnits = -(minY - geometry.centerY) * geometry.scale;
  plane.position.set(centerXUnits, 0.06, frontZUnits + depthUnits / 2 + 0.35);
  plane.renderOrder = 3;
  root.add(plane);
}

function sceneObjectStyleFor(labware: LabwareModel): SceneObjectStyle {
  const kind = hintedSceneObjectKind(labware) || inferSceneObjectKind(labware);
  const liquidCapable = liquidCapableForSceneObject(labware, kind);
  const drySlots = !liquidCapable && drySlotsForSceneObject(labware, kind);
  const palette = sceneObjectPalette(labware, kind);
  return {
    kind,
    liquidCapable,
    drySlots,
    bodyColor: palette.bodyColor,
    edgeColor: palette.edgeColor,
    accentColor: palette.accentColor,
    labelColor: palette.labelColor,
    labelBackground: palette.labelBackground,
    opacity: palette.opacity
  };
}

function hintedSceneObjectKind(labware: LabwareModel): SceneObjectKind | null {
  const hints = labware as LabwareSceneHints;
  return (
    asSceneObjectKind(hints.sceneKind) ||
    asSceneObjectKind(hints.sceneRole) ||
    asSceneObjectKind(labware.role) ||
    asSceneObjectKind(labware.componentRole) ||
    asSceneObjectKind(hints.componentKind) ||
    asSceneObjectKind(hints.componentRole) ||
    asSceneObjectKind(hints.geometryRole) ||
    asSceneObjectKind(hints.fluentRole) ||
    asSceneObjectKind(hints.functionalGroup) ||
    asSceneObjectKind(hints.renderAs)
  );
}

function asSceneObjectKind(value: unknown): SceneObjectKind | null {
  const normalized = String(value || "").toLowerCase().replace(/[_\s]+/g, "-");
  if (normalized.includes("structural") || normalized.includes("structure") || normalized.includes("frame")) return "structural";
  if (normalized.includes("device") || normalized.includes("instrument") || normalized.includes("camera")) return "device";
  if (normalized.includes("wash") || normalized.includes("waste") || normalized.includes("trough")) return "waste";
  if (normalized.includes("tube-rack") || normalized.includes("tuberack")) return "tube-rack";
  if (normalized.includes("tip-box") || normalized.includes("tipbox")) return "tip-box";
  if (normalized.includes("carrier")) return "carrier";
  if (normalized.includes("nest")) return "nest";
  if (normalized.includes("adapter") || normalized.includes("adaptor")) return "adapter";
  if (normalized.includes("labware") || normalized.includes("vessel") || normalized.includes("plate") || normalized.includes("reservoir")) return "labware";
  if (normalized === "labware" || normalized === "vessel" || normalized === "plate" || normalized === "reservoir") return "labware";
  if (normalized === "tube" || normalized === "tube-rack" || normalized === "tuberack") return "tube-rack";
  if (normalized === "tip" || normalized === "tip-box" || normalized === "tipbox") return "tip-box";
  if (normalized === "carrier") return "carrier";
  if (normalized === "nest") return "nest";
  if (normalized === "adapter") return "adapter";
  if (normalized === "device" || normalized === "instrument" || normalized === "camera") return "device";
  if (normalized === "waste" || normalized === "wash" || normalized === "trough") return "waste";
  if (normalized === "structural" || normalized === "structure" || normalized === "frame") return "structural";
  if (normalized === "unknown" || normalized === "generic") return "unknown";
  return null;
}

function inferSceneObjectKind(labware: LabwareModel): SceneObjectKind {
  const text = sceneObjectText(labware);
  if (labware.hardwareProfile === "tip-box" || /\btip(?:s)?\b|tipbox|tip-box/.test(text)) return "tip-box";
  if (isTubeLabware(labware)) return "tube-rack";
  if (isDeviceComponentText(text)) return "device";
  if (/wash|waste|trough|rinse|cleaner|station rl/.test(text)) return "waste";
  // `deck segment`/`base plate` are structural only in the scene view; they stay
  // local rather than in the shared list (they read as deck/carrier elsewhere).
  if (isStructuralComponentText(text) || /deck segment|base plate/.test(text)) return "structural";
  if (labware.hardwareProfile === "adapter" || /adapter|adaptor|spacer|insert|plate nest|landscape nest|portrait nest/.test(text)) return "adapter";
  if (/\bnest\b|cap holder|cap-holder/.test(text) || labware.hardwareProfile === "cap-holder") return "nest";
  if (/carrier|grid|shelf|rack carrier|deck extension|back extension/.test(text)) return "carrier";
  if (labware.wells.length && labware.wellShape !== "none" && (labware.capacityUl > 0 || labware.maxVolumeUl > 0)) return "labware";
  return "unknown";
}

function liquidCapableForSceneObject(labware: LabwareModel, kind: SceneObjectKind): boolean {
  const hints = labware as LabwareSceneHints;
  if (typeof hints.liquidCapable === "boolean") return hints.liquidCapable;
  if (!labware.wells.length || labware.wellShape === "none") return false;
  if (labware.hardwareProfile === "tip-box" || labware.hardwareProfile === "cap-holder" || labware.hardwareProfile === "adapter") return false;
  if (kind === "tip-box" || kind === "carrier" || kind === "nest" || kind === "adapter" || kind === "device" || kind === "structural" || kind === "unknown") return false;
  if (/\btip(?:s)?\b|capholder|cap holder|no tubes|empty holder/.test(sceneObjectText(labware))) return false;
  return labware.capacityUl > 0 || labware.maxVolumeUl > 0 || labware.wells.some((well) => well.capacityUl > 0 || well.maxVolumeUl > 0);
}

function drySlotsForSceneObject(labware: LabwareModel, kind: SceneObjectKind): boolean {
  if (isSiteAnchoredProtocolTarget(labware) && !labware.wells.length) return false;
  if (kind === "tip-box") return true;
  if (labware.wells.length && (kind === "adapter" || kind === "nest" || kind === "carrier" || kind === "unknown")) return true;
  if (labware.hardwareProfile === "cap-holder" || labware.hardwareProfile === "adapter") return true;
  return false;
}

function sceneObjectPalette(labware: LabwareModel, kind: SceneObjectKind): Omit<SceneObjectStyle, "kind" | "liquidCapable" | "drySlots"> {
  if (isSiteAnchoredProtocolTarget(labware)) {
    return {
      bodyColor: labware.color || "#b8d5e2",
      edgeColor: "#5f8ea2",
      accentColor: "#0f6b73",
      labelColor: "#234047",
      labelBackground: "rgba(255,255,255,0.86)",
      opacity: 0.5
    };
  }

  if (kind === "labware" || kind === "tube-rack") {
    return {
      bodyColor: labware.color || "#f7faf8",
      edgeColor: "#8d9794",
      accentColor: "#0f6b73",
      labelColor: "#263332",
      labelBackground: "rgba(255,255,255,0.82)",
      opacity: 1
    };
  }

  if (kind === "tip-box") {
    return {
      bodyColor: "#e7ecea",
      edgeColor: "#9aa8a5",
      accentColor: "#5f7175",
      labelColor: "#263332",
      labelBackground: "rgba(255,255,255,0.82)",
      opacity: 1
    };
  }

  if (kind === "carrier") {
    return {
      bodyColor: "#cfd7d5",
      edgeColor: "#798684",
      accentColor: "#667475",
      labelColor: "#263332",
      labelBackground: "rgba(244,247,246,0.78)",
      opacity: 0.9
    };
  }

  if (kind === "nest" || kind === "adapter") {
    return {
      bodyColor: "#dce3e0",
      edgeColor: "#8b9995",
      accentColor: "#7d8a86",
      labelColor: "#263332",
      labelBackground: "rgba(244,247,246,0.76)",
      opacity: 0.88
    };
  }

  if (kind === "device") {
    return {
      bodyColor: "#bfc9ca",
      edgeColor: "#687576",
      accentColor: "#526265",
      labelColor: "#263332",
      labelBackground: "rgba(244,247,246,0.78)",
      opacity: 1
    };
  }

  if (kind === "waste") {
    return {
      bodyColor: "#d7dfdc",
      edgeColor: "#80908a",
      accentColor: "#4e8a88",
      labelColor: "#263332",
      labelBackground: "rgba(244,247,246,0.78)",
      opacity: 0.92
    };
  }

  if (kind === "structural") {
    return {
      bodyColor: "#c6cecc",
      edgeColor: "#77817f",
      accentColor: "#6d7775",
      labelColor: "#263332",
      labelBackground: "rgba(244,247,246,0.7)",
      opacity: 0.82
    };
  }

  return {
    bodyColor: "#d6ddda",
    edgeColor: "#8a9692",
    accentColor: "#6c7774",
    labelColor: "#263332",
    labelBackground: "rgba(244,247,246,0.72)",
    opacity: 0.76
  };
}

function isSiteAnchoredProtocolTarget(labware: LabwareModel): boolean {
  return labware.role === "protocol_target" && !labware.transform && Boolean(labware.location);
}

function sceneObjectText(labware: LabwareModel): string {
  const hints = labware as LabwareSceneHints;
  const transformText = [labware.transform?.parentLabels.join(" "), labware.transform?.path.join(" ")].filter(Boolean).join(" ");
  return [
    labware.label,
    labware.catalogName,
    labware.geometryName,
    labware.role,
    labware.source,
    labware.location,
    labware.positionExpression,
    labware.hardwareProfile,
    hints.sceneKind,
    hints.sceneRole,
    hints.componentKind,
    hints.componentRole,
    hints.geometryRole,
    hints.fluentRole,
    hints.functionalGroup,
    hints.renderAs,
    hints.modelAssetPath,
    hints.modelAssetFormat,
    hints.meshGuid,
    hints.meshName,
    hints.meshSourcePath,
    (labware.transform as TransformSceneHints | null | undefined)?.modelAssetPath,
    (labware.transform as TransformSceneHints | null | undefined)?.modelAssetFormat,
    (labware.transform as TransformSceneHints | null | undefined)?.meshGuid,
    (labware.transform as TransformSceneHints | null | undefined)?.meshName,
    (labware.transform as TransformSceneHints | null | undefined)?.meshSourcePath,
    transformText
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function addLabware(
  runtime: RuntimeScene,
  root: THREE.Group,
  labware: LabwareModel,
  placement: LabwarePlacement,
  geometry: SceneGeometryContext,
  snapshot: SnapshotModel | undefined,
  activeCommand: CommandModel | undefined,
  focusTarget: SceneFocusTarget | null,
  activeTargetHasPhysicalMatch = false,
  focusTargetHasPhysicalMatch = false,
  highlight: ValidationHighlight | null = null
): void {
  const group = new THREE.Group();
  group.position.copy(placement.position);
  group.rotation.y = placement.rotationY;
  root.add(group);

  const size = placement.size;
  const style = sceneObjectStyleFor(labware);
  const bodyHeight = bodyHeightForLabware(labware, size, style);
  addContactShadow(root, placement, bodyHeight);
  const isActive = activeCommand?.targetLabware ? isPhysicalPreferredTarget(labware, activeCommand.targetLabware, activeTargetHasPhysicalMatch) : false;
  const isFocused = focusTarget?.labwareLabel ? isPhysicalPreferredTarget(labware, focusTarget.labwareLabel, focusTargetHasPhysicalMatch) : false;
  const assetPath = modelAssetPathForLabware(labware);
  const hasInteractiveOverride = Boolean(placementOverrideForLabel(runtime.placementOverrides, labware.label));
  const nativeFluentModel = isNativeFluentModelAsset(labware, assetPath) && !hasInteractiveOverride;
  const modelRenderState = addModelAsset(runtime, root, group, labware, geometry, size, bodyHeight, style, assetPath, nativeFluentModel);
  // A real mesh (native Fluent GLB or fit-to-catalog) already models the holder and
  // its vessels/tips, so procedural wells/slots drawn on top duplicate geometry and
  // cause z-fighting. This must cover the async loading window too — procedural
  // geometry added while the mesh loads is never cleaned up when it arrives. The gate
  // is intentionally independent of `nativeFluentModel` so it also holds for catalog
  // meshes and for parts the user has dragged (which disables the native path but
  // still loads a mesh). Only error/none states fall through to procedural rendering.
  const skipProceduralLabwareGeometry = modelRenderState === "loaded" || modelRenderState === "loading";
  const renderPrimitiveBase = modelRenderState !== "loaded";
  // On the realistic Fluent worktable, a primitive fallback means we have no mesh
  // for an (often abstract/parametric) target. Render it as a clearly-labeled
  // translucent ghost so it doesn't masquerade as solid hardware. Loading states
  // still get a solid placeholder so the spot isn't empty while the mesh arrives.
  const renderAsPlaceholder = renderPrimitiveBase && geometry.hasFluentGeometry && modelRenderState !== "loading";

  if (renderPrimitiveBase) {
    if (renderAsPlaceholder) {
      addPlaceholderFallbackBody(group, labware, size, bodyHeight, style);
    } else {
      addPrimitiveFallbackBody(group, size, bodyHeight, style, isActive, isFocused, geometry.hasFluentGeometry);
    }
  }

  if (renderAsPlaceholder) {
    // A no-mesh placeholder stays a clean labeled ghost; drawing wells/slots/rails
    // would make an abstract target read as fabricated solid hardware.
  } else if (!skipProceduralLabwareGeometry && style.liquidCapable) {
    addWells(group, labware, placement, geometry, snapshot, activeCommand);
  } else if (!skipProceduralLabwareGeometry && style.drySlots) {
    addDrySlots(group, labware, placement, geometry, bodyHeight, activeCommand, style);
  } else if (renderPrimitiveBase) {
    addNonVesselDetails(primitiveFallbackGroupFor(group), labware, size, bodyHeight, style);
  }

  if (modelRenderState === "loading") {
    addModelStatusBadge(group, labware, size, bodyHeight, "Loading model", style.accentColor);
  } else if (modelRenderState === "error") {
    addModelStatusBadge(group, labware, size, bodyHeight, "Model fallback", style.edgeColor);
  }

  if (shouldLabelLabware(labware, style, isActive, isFocused, Boolean(activeCommand?.targetLabware), geometry.hasFluentGeometry)) {
    const label = makeTextSprite(labware.label, isFocused ? "#6d3d06" : isActive ? style.accentColor : style.labelColor, style.labelBackground);
    label.position.set(0, labwareLabelY(labware, size), -size.depth / 2 - 0.4);
    label.scale.set(3.5, 0.52, 1);
    group.add(label);
  }

  if (highlight) {
    addValidationHighlight(group, labware, size, bodyHeight, highlight);
  }

  if (!isVisualOnlyWallObject(labware)) markSceneInteractive(group, labware, placement, true);
}

function isVisualOnlyWallObject(labware: LabwareModel): boolean {
  const text = sceneObjectText(labware);
  if (sceneObjectStyleFor(labware).kind !== "structural") return false;
  // Enclosure walls/frames are transparent visual context. They should not steal
  // hover/focus from the worktable components visible through them. Keep rails,
  // deck segments, shelves, and base plates interactive because those can be real
  // placement/reference geometry.
  return /frame|wall|sidewall|front wall|back wall|rear wall|plexiglas|pane|door|cover/.test(text);
}

function addValidationHighlight(
  group: THREE.Group,
  labware: LabwareModel,
  size: LabwareSceneSize,
  bodyHeight: number,
  highlight: ValidationHighlight
): void {
  const color = validationSeverityColor(highlight.severity);
  const padX = size.width * 0.5 + 0.32;
  const padZ = size.depth * 0.5 + 0.32;
  const height = Math.max(bodyHeight, 0.6) + 0.12;

  const outline = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(padX * 2, height, padZ * 2)),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.95 })
  );
  outline.position.y = height / 2;
  outline.renderOrder = 5;
  group.add(outline);

  const ringGeometry = new THREE.RingGeometry(Math.max(padX, padZ) * 0.92, Math.max(padX, padZ) * 1.04, 36);
  const ring = new THREE.Mesh(
    ringGeometry,
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.32, side: THREE.DoubleSide, depthWrite: false })
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.06;
  ring.renderOrder = 4;
  group.add(ring);

  const badgeText = highlight.severity === "blocking" ? "! Blocking" : "! Review";
  const badge = makeTextSprite(badgeText, "#ffffff", validationSeverityBadgeBackground(highlight.severity));
  badge.position.set(0, labwareLabelY(labware, size) + 0.62, -size.depth / 2 - 0.4);
  badge.scale.set(2.6, 0.58, 1);
  badge.renderOrder = 6;
  group.add(badge);
}

function validationSeverityColor(severity: DiffSeverity): string {
  return severity === "blocking" ? "#d8392b" : "#d68a17";
}

function validationSeverityBadgeBackground(severity: DiffSeverity): string {
  return severity === "blocking" ? "rgba(176,38,28,0.94)" : "rgba(176,108,16,0.94)";
}

function validationHighlightLookup(highlights: ValidationHighlight[]): ValidationHighlightLookup {
  const lookup: ValidationHighlightLookup = new Map();
  highlights.forEach((highlight) => {
    const key = normalizeLabwareLabel(highlight.labwareLabel);
    if (!key) return;
    const existing = lookup.get(key);
    if (!existing || validationSeverityRank(highlight.severity) > validationSeverityRank(existing.severity)) {
      lookup.set(key, highlight);
    }
  });
  return lookup;
}

function validationHighlightForLabware(lookup: ValidationHighlightLookup, labware: LabwareModel): ValidationHighlight | null {
  if (!lookup.size) return null;
  const direct = lookup.get(normalizeLabwareLabel(labware.label));
  if (direct) return direct;
  for (const highlight of lookup.values()) {
    if (labwareLabelMatches(highlight.labwareLabel, labware.label)) return highlight;
  }
  return null;
}

function validationSeverityRank(severity: DiffSeverity): number {
  if (severity === "blocking") return 3;
  if (severity === "needs_review") return 2;
  if (severity === "safe") return 1;
  return 0;
}

function isPhysicalPreferredTarget(labware: LabwareModel, targetLabel: string, hasPhysicalMatch: boolean): boolean {
  if (!labwareLabelMatches(labware.label, targetLabel)) return false;
  if (!hasPhysicalMatch) return true;
  return Boolean(labware.transform);
}

function shouldLabelLabware(labware: LabwareModel, style: SceneObjectStyle, isActive: boolean, isFocused: boolean, hasActiveTarget: boolean, hasFluentGeometry: boolean): boolean {
  if (isActive || isFocused || geometryDebugEnabled()) return true;
  if (hasFluentGeometry) return false;
  if (hasActiveTarget) return false;
  if (style.kind === "carrier" || style.kind === "nest" || style.kind === "adapter" || style.kind === "device" || style.kind === "structural") return false;
  if (!labware.wells.length && !style.liquidCapable && style.kind !== "waste") return false;
  return true;
}

function addPrimitiveFallbackBody(
  group: THREE.Group,
  size: LabwareSceneSize,
  bodyHeight: number,
  style: SceneObjectStyle,
  isActive: boolean,
  isFocused: boolean,
  neutralizeColors = false
): void {
  const fallbackGroup = primitiveFallbackGroupFor(group);
  // On the realistic Fluent worktable, primitive fallbacks (labware without a
  // placed mesh) should adopt the same neutral plastic palette as the meshes so
  // they don't stand out as schematic green/blue blocks vs the FluentControl view.
  const baseBodyColor = neutralizeColors ? fluentMeshNeutralColorForKind(style.kind) : style.bodyColor;
  const baseEdgeColor = neutralizeColors ? "#5b6366" : style.edgeColor;
  const bodyMaterial = new THREE.MeshStandardMaterial({
    color: baseBodyColor,
    roughness: style.kind === "device" ? 0.38 : 0.55,
    metalness: style.kind === "device" || style.kind === "carrier" ? 0.16 : 0.03,
    transparent: style.opacity < 1,
    opacity: style.opacity,
    emissive: isFocused ? "#b77921" : isActive ? style.accentColor : "#000000",
    emissiveIntensity: isFocused ? 0.32 : isActive ? 0.18 : 0
  });
  const body = new THREE.Mesh(new THREE.BoxGeometry(size.width, bodyHeight, size.depth), bodyMaterial);
  body.castShadow = true;
  body.receiveShadow = true;
  body.position.y = bodyHeight / 2;
  fallbackGroup.add(body);

  const edge = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(size.width, bodyHeight + 0.02, size.depth)),
    new THREE.LineBasicMaterial({ color: isFocused ? "#b77921" : isActive ? style.accentColor : baseEdgeColor, linewidth: 1 })
  );
  edge.position.y = bodyHeight / 2 + 0.01;
  fallbackGroup.add(edge);
}

// Renders a labware that has no mesh asset as a translucent, dashed-outline ghost
// with a persistent "no mesh" badge. Used in the Fluent worktable view so abstract
// or parametric targets (e.g. CollectionDWP[platecount], Labware on nest platform)
// are visibly placeholders rather than fabricated solid hardware.
function addPlaceholderFallbackBody(
  group: THREE.Group,
  labware: LabwareModel,
  size: LabwareSceneSize,
  bodyHeight: number,
  style: SceneObjectStyle
): void {
  const fallbackGroup = primitiveFallbackGroupFor(group);
  const height = Math.max(bodyHeight, 0.18);
  const boxGeometry = new THREE.BoxGeometry(size.width, height, size.depth);

  const body = new THREE.Mesh(
    boxGeometry,
    new THREE.MeshStandardMaterial({
      color: "#9aa7ad",
      roughness: 0.7,
      metalness: 0,
      transparent: true,
      opacity: 0.16,
      depthWrite: false,
    })
  );
  body.position.y = height / 2;
  body.renderOrder = 2;
  body.castShadow = false;
  body.receiveShadow = false;
  fallbackGroup.add(body);

  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(boxGeometry),
    new THREE.LineDashedMaterial({
      color: PLACEHOLDER_FALLBACK_ACCENT,
      dashSize: 0.2,
      gapSize: 0.13,
      transparent: true,
      opacity: 0.92,
    })
  );
  edges.computeLineDistances();
  edges.position.y = height / 2;
  edges.renderOrder = 3;
  fallbackGroup.add(edges);

  const badge = makeTextSprite(`${labware.label}  ·  no mesh`, "#0f2b33", "rgba(214,238,244,0.92)");
  badge.position.set(0, height + 0.46, 0);
  badge.scale.set(3.1, 0.46, 1);
  badge.renderOrder = 6;
  fallbackGroup.add(badge);
}

function primitiveFallbackGroupFor(group: THREE.Group): THREE.Group {
  const existing = group.children.find((child): child is THREE.Group => child.name === MODEL_PRIMITIVE_FALLBACK_NAME && child instanceof THREE.Group);
  if (existing) return existing;

  const fallbackGroup = new THREE.Group();
  fallbackGroup.name = MODEL_PRIMITIVE_FALLBACK_NAME;
  group.add(fallbackGroup);
  return fallbackGroup;
}

function modelAssetSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "item";
}

function pathBasename(pathOrName: string): string {
  return pathOrName.split(/[\\/]/).filter(Boolean).pop() || pathOrName;
}

function addModelAsset(
  runtime: RuntimeScene,
  root: THREE.Group,
  group: THREE.Group,
  labware: LabwareModel,
  geometry: SceneGeometryContext,
  size: LabwareSceneSize,
  bodyHeight: number,
  style: SceneObjectStyle,
  assetPath: string,
  nativeFluentModel: boolean
): ModelRenderState {
  if (!assetPath) return "none";
  const modelParent = nativeFluentModel ? root : group;

  // Places a freshly cloned copy of the loaded source mesh into this build's
  // group. Returns true when the mesh was accepted and added to the scene.
  const placeIntoGroup = (source: THREE.Object3D | null): boolean => {
    if (runtime.disposed || !group.parent) return false;
    if (!source) {
      // The load failed: replace the "loading" badge with an honest fallback
      // marker so the group doesn't sit on a perpetual loading state.
      removeModelStatusBadges(group);
      addModelStatusBadge(group, labware, size, bodyHeight, "Model unavailable", style.edgeColor);
      runtime.renderer.render(runtime.scene, runtime.camera);
      return false;
    }
    const model = cloneModelAsset(source);
    applyFluentMeshAppearance(model, style, nativeFluentModel);
    const accepted = placeModelAsset(model, labware, geometry, size, bodyHeight, nativeFluentModel);
    if (!accepted) {
      disposeObject(model);
      addModelStatusBadge(group, labware, size, bodyHeight, "Model fallback", style.edgeColor);
      return false;
    }
    if (!isVisualOnlyWallObject(labware)) markSceneInteractive(model, labware, undefined, true);
    removeModelStatusBadges(group);
    removePrimitiveFallback(group);
    modelParent.add(model);
    runtime.renderer.render(runtime.scene, runtime.camera);
    return true;
  };

  const loadPath = runtimeModelAssetUrl(assetPath);
  const cached = runtime.modelCache.get(loadPath);
  if (cached?.status === "loaded" && cached.scene) {
    return placeIntoGroup(cached.scene) ? "loaded" : "error";
  }
  if (cached?.status === "error") return "error";
  if (cached?.status === "loading") {
    // Asset is still loading from an earlier build. Register this build's group
    // so it receives the mesh when the load resolves (avoids orphaned meshes).
    (cached.waiters ||= []).push(placeIntoGroup);
    return "loading";
  }

  const entry: ModelCacheEntry = { status: "loading", waiters: [placeIntoGroup] };
  runtime.modelCache.set(loadPath, entry);
  runtime.gltfLoader.load(
    loadPath,
    (gltf) => {
      const source = gltf.scene || new THREE.Group();
      applyModelAssetMetadata(source, gltf);
      prepareModelAssetSource(source);
      entry.status = "loaded";
      entry.scene = source;
      const waiters = entry.waiters || [];
      entry.waiters = [];
      // Component unmounted while the load was in flight: keep the cached source
      // (a remount can reuse it) but don't touch the disposed runtime/scene.
      if (runtime.disposed) return;
      waiters.forEach((waiter) => waiter(source));
      runtime.renderer.render(runtime.scene, runtime.camera);
    },
    undefined,
    (error) => {
      entry.status = "error";
      entry.error = error;
      const waiters = entry.waiters || [];
      entry.waiters = [];
      if (runtime.disposed) return;
      // Let the groups that were waiting on this asset show a fallback marker
      // instead of remaining stuck on the "loading" badge.
      waiters.forEach((waiter) => waiter(null));
      runtime.renderer.render(runtime.scene, runtime.camera);
    }
  );

  return "loading";
}

function removePrimitiveFallback(group: THREE.Group): void {
  const fallbacks = group.children.filter((child) => child.name === MODEL_PRIMITIVE_FALLBACK_NAME);
  fallbacks.forEach((fallback) => {
    group.remove(fallback);
    disposeObject(fallback);
  });
}

function applyModelAssetMetadata(source: THREE.Object3D, gltf: unknown): void {
  const extras = modelAssetExtras(gltf);
  const unitScaleToMm = finitePositive(extras.unitScaleToMm);
  if (unitScaleToMm) source.userData.fluentUnitScaleToMm = unitScaleToMm;
  if (typeof extras.unitScaleSource === "string") source.userData.fluentUnitScaleSource = extras.unitScaleSource;
  if (typeof extras.nativeUnit === "string") source.userData.fluentNativeUnit = extras.nativeUnit;
}

function modelAssetExtras(gltf: unknown): Record<string, unknown> {
  const json = ((gltf as { parser?: { json?: Record<string, unknown> } }).parser?.json || {}) as Record<string, unknown>;
  const sceneIndex = Number(json.scene ?? 0);
  const scenes = Array.isArray(json.scenes) ? (json.scenes as Array<Record<string, unknown>>) : [];
  const nodes = Array.isArray(json.nodes) ? (json.nodes as Array<Record<string, unknown>>) : [];
  return {
    ...recordExtras(json),
    ...recordExtras(scenes[sceneIndex]),
    ...nodes.reduce<Record<string, unknown>>((merged, node) => ({ ...merged, ...recordExtras(node) }), {})
  };
}

function recordExtras(record: unknown): Record<string, unknown> {
  const extras = (record as { extras?: unknown } | undefined)?.extras;
  return extras && typeof extras === "object" && !Array.isArray(extras) ? (extras as Record<string, unknown>) : {};
}

function prepareModelAssetSource(source: THREE.Object3D): void {
  source.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
  });
}

function fluentMeshNeutralColorForKind(kind: SceneObjectKind): string {
  // Neutral, FluentControl-like plastic palette. The synthetic categorical colors
  // (green tube runners, teal/amber labware) the parser assigns for schematic
  // color-coding are intentionally ignored here so the realistic mesh view matches
  // the gray/charcoal worktable FluentControl renders.
  switch (kind) {
    case "structural":
      return "#646b6e";
    case "carrier":
      return "#6f7679";
    case "adapter":
    case "nest":
      return "#828b8c";
    case "waste":
      return "#7c8486";
    case "device":
      return "#aab2b2";
    case "tube-rack":
      return "#b7beba";
    case "tip-box":
      return "#d7ddda";
    case "labware":
      return "#c9d0cd";
    default:
      return "#a7afad";
  }
}

// Plate-like labware (microplates, tube racks, tip boxes) keeps the categorical
// color FluentControl uses for at-a-glance identification (blue collection plates,
// green source tubes, etc.). Carriers, nests, devices, waste, and structural deck
// parts stay neutral gray to match the worktable chassis.
const FLUENT_MESH_COLOR_CODED_KINDS: ReadonlySet<SceneObjectKind> = new Set<SceneObjectKind>([
  "labware",
  "tube-rack",
  "tip-box",
]);

function fluentMeshAppearanceBaseColor(style: SceneObjectStyle): { color: THREE.Color; categorical: boolean } {
  if (FLUENT_MESH_COLOR_CODED_KINDS.has(style.kind) && style.bodyColor) {
    const categorical = new THREE.Color(style.bodyColor);
    // Reject near-white/near-gray "colors" so plates without a real category hue
    // still fall back to the neutral plastic ramp instead of looking washed out.
    const hsl = { h: 0, s: 0, l: 0 };
    categorical.getHSL(hsl);
    if (hsl.s >= 0.08) return { color: categorical, categorical: true };
  }
  return { color: new THREE.Color(fluentMeshNeutralColorForKind(style.kind)), categorical: false };
}

function applyFluentMeshAppearance(model: THREE.Object3D, style: SceneObjectStyle, nativeFluentModel: boolean): void {
  if (!nativeFluentModel) return;
  // Extracted Fluent .xmsh meshes lose their native colors during conversion
  // (most arrive as pure black or flat gray). Repaint them while preserving each
  // part's relative light/dark detail so recesses and raised features still read
  // like the FluentControl worktable view. Plate-like labware uses its categorical
  // color; chassis parts use the neutral plastic ramp.
  const { color: base, categorical } = fluentMeshAppearanceBaseColor(style);
  // Colored labware keeps a higher brightness floor so the hue stays vivid instead
  // of being crushed toward black by dark source meshes.
  const minFactor = categorical ? 0.82 : 0.58;
  const maxFactor = categorical ? 1.1 : 1.18;
  // The cabinet frame / walls are large perimeter panels. Rendering them solid
  // occludes the deck from the default 3/4 view, so they read as a translucent
  // glass enclosure (and don't cast big shadows) instead of opaque slabs.
  const structural = style.kind === "structural";
  model.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (structural) {
      mesh.castShadow = false;
      mesh.receiveShadow = false;
    }
    const recolor = (material: THREE.Material): THREE.Material => {
      const std = (material as THREE.MeshStandardMaterial).isMeshStandardMaterial
        ? (material as THREE.MeshStandardMaterial)
        : new THREE.MeshStandardMaterial();
      const luminance = std.color ? std.color.r * 0.299 + std.color.g * 0.587 + std.color.b * 0.114 : 0;
      const detailFactor = Math.min(maxFactor, Math.max(minFactor, 0.62 + luminance * 0.62));
      const target = base.clone().multiplyScalar(detailFactor);
      std.color = target;
      if (std.emissive) std.emissive.setRGB(0, 0, 0);
      std.roughness = categorical ? 0.5 : 0.62;
      std.metalness = 0.06;
      std.vertexColors = false;
      std.flatShading = false;
      if (structural) {
        std.transparent = true;
        std.opacity = 0.16;
        std.depthWrite = false;
        std.side = THREE.DoubleSide;
      }
      std.needsUpdate = true;
      return std;
    };
    if (Array.isArray(mesh.material)) mesh.material = mesh.material.map(recolor);
    else if (mesh.material) mesh.material = recolor(mesh.material);
  });
}

function cloneModelAsset(source: THREE.Object3D): THREE.Group {
  const clone = source.clone(true) as THREE.Group;
  clone.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (mesh.geometry) mesh.geometry = mesh.geometry.clone();
    const material = mesh.material;
    if (Array.isArray(material)) mesh.material = material.map((item) => item.clone());
    else if (material) mesh.material = material.clone();
    mesh.castShadow = true;
    mesh.receiveShadow = true;
  });
  return clone;
}

function placeModelAsset(
  model: THREE.Group,
  labware: LabwareModel,
  geometry: SceneGeometryContext,
  size: LabwareSceneSize,
  bodyHeight: number,
  nativeFluentModel: boolean
): boolean {
  if (nativeFluentModel && geometry.hasFluentGeometry && labware.transform) {
    return placeNativeFluentModelAsset(model, labware, geometry);
  }
  fitModelAssetToLabware(model, labware, size, bodyHeight);
  return true;
}

function placeNativeFluentModelAsset(model: THREE.Group, labware: LabwareModel, geometry: SceneGeometryContext): boolean {
  const transform = labware.transform;
  if (!transform) return false;

  const unitScaleToMm = nativeFluentUnitScaleToMm(model, labware);
  // When a mesh is modeled at a different scale than its declared footprint we
  // keep the real geometry (instead of dropping to a primitive) and fit-correct
  // it to the labware dimensions so it sits correctly without overlapping.
  const fitCorrection = nativeFluentModelBoundsMismatch(model, labware, unitScaleToMm)
    ? nativeFluentFitCorrection(model, labware, unitScaleToMm)
    : 1;

  model.position.copy(fluentToThree(transform.positionMm, geometry));
  // The fluent orientation remap (R) assumes the mesh is in the fluent Z-up
  // object frame, but the converted .glb meshes are exported Y-up (glTF). Apply
  // R * M * R^-1 by post-multiplying the Y-up -> Z-up correction so the mesh's
  // own axes line up before the orientation is applied (otherwise carriers stand
  // up as vertical walls instead of lying flat on the deck).
  model.quaternion.copy(quaternionFromFluentOrientation(transform.orientation)).multiply(FLUENT_MESH_YUP_CORRECTION);
  model.scale.multiplyScalar(geometry.scale * unitScaleToMm * fitCorrection);
  const lateralFit = nativeFluentVesselLateralFit(model, labware, unitScaleToMm);
  if (lateralFit) {
    model.scale.set(model.scale.x * lateralFit.x, model.scale.y * lateralFit.y, model.scale.z * lateralFit.z);
  }
  model.userData.modelAssetPath = modelAssetPathForLabware(labware);
  model.userData.nativeFluentModel = true;
  model.userData.fluentOriginMm = transform.positionMm;
  model.userData.fluentUnitScaleToMm = unitScaleToMm;
  model.userData.fluentFitCorrection = fitCorrection;
  return true;
}

// For standing vessels (tubes/vials) whose converted mesh is modeled thinner
// than the declared labware footprint, returns a per-axis multiplier that fattens
// the two lateral axes to the declared cross-section while leaving the vertical
// (height) axis untouched. Returns null for non-vessel or already-correct meshes.
function nativeFluentVesselLateralFit(
  model: THREE.Group,
  labware: LabwareModel,
  unitScaleToMm: number
): THREE.Vector3 | null {
  const declared = nativeFluentTargetSizeMm(labware);
  // sortedPositiveDimensions returns largest-first: [0]=longest, [1]/[2]=lateral.
  const declaredSorted = sortedPositiveDimensions(declared);
  if (declaredSorted.length < 3) return null;
  // Vessel-like = a narrow square-ish cross-section with a much longer third axis.
  const crossRatio = declaredSorted[1] / declaredSorted[2];
  const elongation = declaredSorted[0] / declaredSorted[1];
  if (crossRatio > 1.6 || elongation < 1.8) return null;

  const orientation = model.quaternion.clone();
  const priorScale = model.scale.clone();
  model.quaternion.identity();
  model.scale.set(1, 1, 1);
  model.updateMatrixWorld(true);
  const nativeSize = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3());
  model.quaternion.copy(orientation);
  model.scale.copy(priorScale);

  const axes = [nativeSize.x, nativeSize.y, nativeSize.z];
  if (axes.some((value) => value <= 1e-6)) return null;
  let longestAxis = 0;
  for (let index = 1; index < 3; index += 1) {
    if (axes[index] > axes[longestAxis]) longestAxis = index;
  }
  // The long axis must stand vertically once oriented, otherwise it is a flat
  // carrier/runner that happens to be elongated and must not be distorted.
  const longestDir = new THREE.Vector3();
  longestDir.setComponent(longestAxis, 1);
  longestDir.applyQuaternion(orientation).normalize();
  if (Math.abs(longestDir.dot(SCENE_WORLD_UP)) < 0.7) return null;

  const lateralTargetMm = (declaredSorted[1] + declaredSorted[2]) / 2;
  const scale = new THREE.Vector3(1, 1, 1);
  let adjusted = false;
  for (let index = 0; index < 3; index += 1) {
    if (index === longestAxis) continue;
    const renderedMm = axes[index] * unitScaleToMm;
    if (renderedMm <= 1e-6) continue;
    let ratio = lateralTargetMm / renderedMm;
    if (!Number.isFinite(ratio)) continue;
    ratio = Math.min(VESSEL_LATERAL_FIT_MAX, Math.max(VESSEL_LATERAL_FIT_MIN, ratio));
    if (Math.abs(ratio - 1) >= 0.05) adjusted = true;
    scale.setComponent(index, ratio);
  }
  return adjusted ? scale : null;
}

function nativeFluentFitCorrection(model: THREE.Group, labware: LabwareModel, unitScaleToMm: number): number {
  const targetSorted = sortedPositiveDimensions(nativeFluentTargetSizeMm(labware));
  if (targetSorted.length < 2) return 1;

  model.updateMatrixWorld(true);
  const sourceSize = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3()).multiplyScalar(unitScaleToMm);
  const sourceSorted = sortedPositiveDimensions(sourceSize);
  if (sourceSorted.length < 2) return 1;

  const ratios: number[] = [];
  const pairs = Math.min(sourceSorted.length, targetSorted.length);
  for (let index = 0; index < pairs; index += 1) {
    const ratio = targetSorted[index] / sourceSorted[index];
    if (Number.isFinite(ratio) && ratio > 0) ratios.push(ratio);
  }
  if (!ratios.length) return 1;
  ratios.sort((a, b) => a - b);
  const median = ratios[Math.floor(ratios.length / 2)];
  if (!Number.isFinite(median) || median <= 0) return 1;
  // Leave near-correct meshes untouched; only rescale clear mismatches.
  return median > 0.8 && median < 1.25 ? 1 : median;
}

function quaternionFromFluentOrientation(matrix: Matrix3Model): THREE.Quaternion {
  const xAxis = normalizeBasisVector(new THREE.Vector3(matrix[0][0], matrix[2][0], -matrix[1][0]));
  const yAxis = normalizeBasisVector(new THREE.Vector3(matrix[0][1], matrix[2][1], -matrix[1][1]));
  const zAxis = normalizeBasisVector(new THREE.Vector3(matrix[0][2], matrix[2][2], -matrix[1][2]));
  const basis = new THREE.Matrix4().makeBasis(xAxis, yAxis, zAxis);
  return new THREE.Quaternion().setFromRotationMatrix(basis);
}

function normalizeBasisVector(vector: THREE.Vector3): THREE.Vector3 {
  return vector.lengthSq() > 1e-8 ? vector.normalize() : vector.set(0, 1, 0);
}

function nativeFluentUnitScaleToMm(model: THREE.Group, labware: LabwareModel): number {
  const recorded = recordedFluentUnitScaleToMm(model, labware);
  if (recorded) return recorded;

  model.updateMatrixWorld(true);
  const sourceSize = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3());
  const targetSize = nativeFluentTargetSizeMm(labware);
  const candidates = [1, 10, 100, 1000];
  const scored = candidates
    .map((candidate) => ({ candidate, score: unitScaleScore(sourceSize, targetSize, candidate) }))
    .sort((a, b) => a.score - b.score);
  const best = scored[0];
  if (best && Number.isFinite(best.score) && best.score < 0.72) return best.candidate;

  const sourceMax = Math.max(sourceSize.x, sourceSize.y, sourceSize.z);
  return sourceMax > 0 && sourceMax < 2 ? 1000 : 1;
}

function recordedFluentUnitScaleToMm(model: THREE.Object3D, labware: LabwareModel): number | null {
  const hints = labware as LabwareSceneHints;
  const transformHints = labware.transform as TransformSceneHints | null | undefined;
  return (
    finitePositive(model.userData.fluentUnitScaleToMm) ||
    finitePositive(model.userData.unitScaleToMm) ||
    finitePositive(hints.unitScaleToMm) ||
    finitePositive(transformHints?.unitScaleToMm)
  );
}

function nativeFluentTargetSizeMm(labware: LabwareModel): THREE.Vector3 {
  const dimension = labware.transform?.dimensionMm;
  return new THREE.Vector3(
    finitePositive(dimension?.x) || finitePositive(labware.physicalWidthMm) || 0,
    finitePositive(dimension?.y) || finitePositive(labware.physicalDepthMm) || 0,
    finitePositive(dimension?.z) || finitePositive(labware.physicalHeightMm) || 0
  );
}

function nativeFluentModelBoundsMismatch(model: THREE.Group, labware: LabwareModel, unitScaleToMm: number): boolean {
  const targetSize = nativeFluentTargetSizeMm(labware);
  const targetSorted = sortedPositiveDimensions(targetSize);
  if (targetSorted.length < 2) return false;

  model.updateMatrixWorld(true);
  const sourceSize = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3()).multiplyScalar(unitScaleToMm);
  const sourceSorted = sortedPositiveDimensions(sourceSize);
  if (sourceSorted.length < 2) return false;

  const largestRatio = sourceSorted[0] / targetSorted[0];
  const middleRatio = sourceSorted[1] / targetSorted[1];
  const volumeRatio = dimensionVolume(sourceSorted) / Math.max(1, dimensionVolume(targetSorted));
  return (
    (largestRatio > NATIVE_MODEL_LARGEST_RATIO_LIMIT && middleRatio > NATIVE_MODEL_MIDDLE_RATIO_LIMIT) ||
    (largestRatio > NATIVE_MODEL_MIDDLE_RATIO_LIMIT && volumeRatio > NATIVE_MODEL_VOLUME_RATIO_LIMIT)
  );
}

function sortedPositiveDimensions(vector: THREE.Vector3): number[] {
  return [vector.x, vector.y, vector.z].filter((value) => Number.isFinite(value) && value > 0).sort((a, b) => b - a);
}

function dimensionVolume(dimensions: number[]): number {
  return dimensions.reduce((product, dimension) => product * Math.max(1, dimension), 1);
}

function unitScaleScore(sourceSize: THREE.Vector3, targetSize: THREE.Vector3, candidate: number): number {
  const pairs: Array<[number, number]> = [
    [sourceSize.x, targetSize.x],
    [sourceSize.y, targetSize.y],
    [sourceSize.z, targetSize.z]
  ].filter(([source, target]) => source > 0 && target > 0) as Array<[number, number]>;
  if (!pairs.length) return Infinity;
  const score = pairs.reduce((sum, [source, target]) => sum + Math.abs(Math.log10(target / source / candidate)), 0);
  return score / pairs.length;
}

function fitModelAssetToLabware(model: THREE.Group, labware: LabwareModel, size: LabwareSceneSize, bodyHeight: number): void {
  applyModelAssetTransformHints(model, labware);
  model.updateMatrixWorld(true);

  const box = new THREE.Box3().setFromObject(model);
  const sourceSize = box.getSize(new THREE.Vector3());
  if (sourceSize.x <= 0 || sourceSize.y <= 0 || sourceSize.z <= 0) {
    model.position.y = 0.02;
    return;
  }

  const targetHeight = Math.max(bodyHeight, size.height, 0.18);
  const scale = Math.min(size.width / sourceSize.x, targetHeight / sourceSize.y, size.depth / sourceSize.z);
  if (Number.isFinite(scale) && scale > 0) model.scale.multiplyScalar(scale * 0.98);
  model.updateMatrixWorld(true);

  const scaledBox = new THREE.Box3().setFromObject(model);
  const center = scaledBox.getCenter(new THREE.Vector3());
  const offset = modelOffsetForLabware(labware, size);
  model.position.add(new THREE.Vector3(-center.x + offset.x, -scaledBox.min.y + 0.02 + offset.y, -center.z + offset.z));
  model.userData.modelAssetPath = modelAssetPathForLabware(labware);
}

function applyModelAssetTransformHints(model: THREE.Object3D, labware: LabwareModel): void {
  const hints = labware as LabwareSceneHints;
  const scale = hints.modelScale;
  if (Array.isArray(scale) && scale.length === 3) {
    model.scale.multiply(new THREE.Vector3(numberOr(scale[0], 1), numberOr(scale[1], 1), numberOr(scale[2], 1)));
  }

  const rotationDeg = hints.modelRotationDeg;
  if (Array.isArray(rotationDeg) && rotationDeg.length === 3) {
    model.rotation.x += THREE.MathUtils.degToRad(numberOr(rotationDeg[0], 0));
    model.rotation.y += THREE.MathUtils.degToRad(numberOr(rotationDeg[1], 0));
    model.rotation.z += THREE.MathUtils.degToRad(numberOr(rotationDeg[2], 0));
  }
}

function modelOffsetForLabware(labware: LabwareModel, size: LabwareSceneSize): THREE.Vector3 {
  const hints = labware as LabwareSceneHints;
  const offset = hints.modelOffsetMm;
  if (!Array.isArray(offset) || offset.length !== 3) return new THREE.Vector3();

  const xScale = finitePositive(labware.physicalWidthMm) ? size.width / Number(labware.physicalWidthMm) : SCENE_MM_SCALE;
  const yScale = finitePositive(labware.physicalHeightMm) ? size.height / Number(labware.physicalHeightMm) : SCENE_MM_SCALE;
  const zScale = finitePositive(labware.physicalDepthMm) ? size.depth / Number(labware.physicalDepthMm) : SCENE_MM_SCALE;
  return new THREE.Vector3(numberOr(offset[0], 0) * xScale, numberOr(offset[1], 0) * yScale, numberOr(offset[2], 0) * zScale);
}

function modelAssetPathForLabware(labware: LabwareModel): string {
  const hints = labware as LabwareSceneHints;
  const directPath = normalizedModelAssetPath(hints.modelAssetPath || (labware.transform as TransformSceneHints | null | undefined)?.modelAssetPath || "");
  if (directPath) return directPath;

  const meshGuid = sanitizeGuid(hints.meshGuid || (labware.transform as TransformSceneHints | null | undefined)?.meshGuid || "");
  if (meshGuid) return `${MODEL_ASSET_DIRECTORY}/${meshGuid}.glb`;

  const meshName = hints.meshName || (labware.transform as TransformSceneHints | null | undefined)?.meshName || "";
  const sluggedName = modelAssetSlug(meshName);
  return sluggedName && sluggedName !== "item" ? `${MODEL_ASSET_DIRECTORY}/${sluggedName}.glb` : "";
}

function isNativeFluentModelAsset(labware: LabwareModel, assetPath: string): boolean {
  if (!labware.transform || !assetPath) return false;
  const hints = labware as LabwareSceneHints;
  const meshGuid = sanitizeGuid(hints.meshGuid || (labware.transform as TransformSceneHints | null | undefined)?.meshGuid || "");
  return Boolean(meshGuid && normalizedModelAssetPath(assetPath).startsWith(`${MODEL_ASSET_DIRECTORY}/`));
}

function normalizedModelAssetPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "";
  const extension = extensionForPath(trimmed);
  if (!MODEL_ASSET_EXTENSIONS.has(extension)) return "";
  if (/^(?:https?:)?\/\//i.test(trimmed) || trimmed.startsWith("/")) return trimmed;
  return `/${trimmed.replace(/^\/+/, "")}`;
}

function runtimeModelAssetUrl(assetPath: string): string {
  const normalized = normalizedModelAssetPath(assetPath);
  if (!normalized || /^(?:https?:)?\/\//i.test(normalized)) return normalized;
  const viteEnv = (import.meta as unknown as { env?: { BASE_URL?: string } }).env;
  const base = (viteEnv?.BASE_URL || "/").replace(/\/+$/, "");
  return base && base !== "" && base !== "/" && normalized.startsWith("/") ? `${base}${normalized}` : normalized;
}

function extensionForPath(path: string): string {
  const cleanPath = path.split(/[?#]/)[0] || path;
  const basename = cleanPath.split(/[\\/]/).pop() || cleanPath;
  const index = basename.lastIndexOf(".");
  return index >= 0 ? basename.slice(index).toLowerCase() : "";
}

function sanitizeGuid(value: string): string {
  const match = value.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  return match?.[0].toLowerCase() || "";
}

function addModelStatusBadge(
  group: THREE.Group,
  labware: LabwareModel,
  size: LabwareSceneSize,
  bodyHeight: number,
  text: string,
  color: string
): void {
  if (!geometryDebugEnabled()) return;
  const meshName = (labware as LabwareSceneHints).meshName || (labware.transform as TransformSceneHints | null | undefined)?.meshName || "";
  const label = makeTextSprite(meshName ? `${text}: ${meshName}` : text, color, "rgba(255,255,255,0.78)");
  label.name = "model-status-badge";
  label.position.set(0, Math.max(bodyHeight, size.height) + 0.86, size.depth / 2 + 0.34);
  label.scale.set(3.5, 0.48, 1);
  group.add(label);
}

function removeModelStatusBadges(group: THREE.Group): void {
  const badges = group.children.filter((child) => child.name === "model-status-badge");
  badges.forEach((badge) => {
    group.remove(badge);
    disposeObject(badge);
  });
}

function numberOr(value: unknown, fallback: number): number {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : fallback;
}

function bodyHeightForSceneObject(size: LabwareSceneSize, style: SceneObjectStyle): number {
  const baseHeight = Math.max(0.12, size.height);
  if (style.kind === "carrier") return clamp(baseHeight, 0.16, 0.52);
  if (style.kind === "nest" || style.kind === "adapter") return clamp(baseHeight, 0.12, 0.42);
  if (style.kind === "tip-box") return clamp(baseHeight, 0.28, 0.88);
  if (style.kind === "structural") return clamp(baseHeight, 0.16, MAX_BODY_HEIGHT);
  if (style.kind === "device") return clamp(baseHeight, 0.55, MAX_BODY_HEIGHT);
  return baseHeight;
}

function bodyHeightForLabware(labware: LabwareModel, size: LabwareSceneSize, style: SceneObjectStyle): number {
  if (isSiteAnchoredProtocolTarget(labware)) return clamp(size.height, 0.14, 0.32);
  return bodyHeightForSceneObject(size, style);
}

function addDrySlots(
  group: THREE.Group,
  labware: LabwareModel,
  placement: LabwarePlacement,
  geometry: SceneGeometryContext,
  bodyHeight: number,
  activeCommand: CommandModel | undefined,
  style: SceneObjectStyle
): void {
  const size = placement.size;
  const selected = selectedWellsForLabware(labware, activeCommand);
  const slotMaterial = new THREE.MeshStandardMaterial({ color: "#eef3f1", roughness: 0.52, metalness: 0.02 });
  const selectedMaterial = new THREE.MeshStandardMaterial({ color: "#f7f1c4", roughness: 0.48, metalness: 0.02 });
  const postMaterial = new THREE.MeshStandardMaterial({ color: style.accentColor, roughness: 0.38, metalness: 0.08, transparent: true, opacity: 0.72 });

  if (labware.wells.length) {
    const layout = wellLayoutFor(labware, size);
    const slotGeometry = new THREE.CylinderGeometry(Math.max(0.035, layout.radius * 0.56), Math.max(0.035, layout.radius * 0.56), 0.05, 14);
    labware.wells.forEach((well) => {
      const position = localWellPositionForPlacement(well, labware, placement, geometry, layout);
      const isSelected = selected.has(well.id);
      const slot = new THREE.Mesh(slotGeometry, isSelected ? selectedMaterial : slotMaterial);
      slot.position.set(position.x, bodyHeight + 0.045, position.z);
      group.add(slot);

      if (style.kind === "tip-box" && (isSelected || !selected.size)) {
        const post = new THREE.Mesh(new THREE.CylinderGeometry(layout.radius * 0.22, layout.radius * 0.34, 0.28, 12), postMaterial);
        post.position.set(position.x, bodyHeight + 0.2, position.z);
        group.add(post);
      }
    });
    addSelectedWellRectangle(group, labware, placement, geometry, layout, selected);
    return;
  }

  if (addFluentSnapSiteDrySlots(group, labware, placement, bodyHeight, slotMaterial, selectedMaterial)) return;

  const rows = finiteCount(labware.rows) || (style.kind === "tip-box" ? 8 : 2);
  const cols = finiteCount(labware.cols) || (style.kind === "tip-box" ? 12 : 6);
  const spanX = Math.max(0.2, size.width * 0.76);
  const spanZ = Math.max(0.2, size.depth * 0.68);
  const stepX = cols > 1 ? spanX / (cols - 1) : 0;
  const stepZ = rows > 1 ? spanZ / (rows - 1) : 0;
  const radius = finiteDimension(Math.min(0.12, Math.max(0.045, Math.min(stepX || 0.12, stepZ || 0.12) * 0.35)), 0.06, 0.035, 0.14);
  const slotGeometry = new THREE.CylinderGeometry(radius, radius, 0.05, 12);

  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const x = cols === 1 ? 0 : -spanX / 2 + col * stepX;
      const z = rows === 1 ? 0 : -spanZ / 2 + row * stepZ;
      const slot = new THREE.Mesh(slotGeometry, slotMaterial);
      slot.position.set(x, bodyHeight + 0.045, z);
      group.add(slot);
    }
  }
}

function addFluentSnapSiteDrySlots(
  group: THREE.Group,
  labware: LabwareModel,
  placement: LabwarePlacement,
  bodyHeight: number,
  slotMaterial: THREE.Material,
  selectedMaterial: THREE.Material
): boolean {
  const sites = (labware.snapSites || []).filter((site) => site.positionMm && isFiniteVector(site.positionMm));
  if (!sites.length) return false;
  const sitePositions = sites
    .map((site) => {
      const local = localFluentWellPosition(site.positionMm as Vector3Model, labware, placement.size);
      return local ? { site, local } : null;
    })
    .filter((item): item is { site: SnapSiteModel; local: THREE.Vector3 } => Boolean(item));
  if (!sitePositions.length) return false;

  const railMaterial = new THREE.MeshStandardMaterial({ color: "#8d9697", roughness: 0.48, metalness: 0.08 });
  const railWidth = Math.max(0.045, Math.min(0.09, placement.size.depth * 0.035));
  [-0.36, 0.36].forEach((offset) => {
    const rail = new THREE.Mesh(new THREE.BoxGeometry(placement.size.width * 0.9, 0.08, railWidth), railMaterial);
    rail.position.set(0, bodyHeight + 0.06, offset * placement.size.depth);
    group.add(rail);
  });

  sitePositions.forEach(({ site, local }) => {
    const siteDimension = site.siteDimensionMm;
    const widthMm = finitePositive(siteDimension?.x) || 18;
    const depthMm = finitePositive(siteDimension?.y) || widthMm;
    const holderDimension = labware.transform?.dimensionMm || {
      x: labware.physicalWidthMm,
      y: labware.physicalDepthMm,
      z: labware.physicalHeightMm
    };
    const xScale = placement.size.width / Math.max(1, finitePositive(holderDimension.x) || labware.physicalWidthMm || 1);
    const zScale = placement.size.depth / Math.max(1, finitePositive(holderDimension.y) || labware.physicalDepthMm || 1);
    const radius = clamp(Math.min(widthMm * xScale, depthMm * zScale) * 0.28, 0.045, 0.18);
    const slot = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, 0.055, 16), slotMaterial);
    slot.position.set(local.x, bodyHeight + 0.08, local.z);
    group.add(slot);

    const rim = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.CylinderGeometry(radius * 1.18, radius * 1.18, 0.065, 16)),
      new THREE.LineBasicMaterial({ color: "#5f6b6d", transparent: true, opacity: 0.78 })
    );
    rim.position.copy(slot.position);
    group.add(rim);
  });

  return true;
}

function addNonVesselDetails(group: THREE.Group, labware: LabwareModel, size: LabwareSceneSize, bodyHeight: number, style: SceneObjectStyle): void {
  if (style.kind === "carrier") {
    addCarrierRails(group, size, bodyHeight, style);
    return;
  }

  if (style.kind === "nest" || style.kind === "adapter") {
    addNestOrAdapterDetail(group, size, bodyHeight, style);
    return;
  }

  if (style.kind === "device") {
    addDeviceDetail(group, size, bodyHeight, style);
    return;
  }

  if (style.kind === "structural") {
    addStructuralDetail(group, size, bodyHeight, style);
    return;
  }

  if (style.kind === "waste") {
    addWasteDetail(group, size, bodyHeight, style);
    return;
  }

  const block = new THREE.Mesh(
    new THREE.BoxGeometry(Math.max(1.2, size.width * 0.62), 0.38, Math.max(0.8, size.depth * 0.5)),
    new THREE.MeshStandardMaterial({ color: "#edf0ee", roughness: 0.7 })
  );
  block.position.y = bodyHeight + 0.2;
  group.add(block);
}

function addCarrierRails(group: THREE.Group, size: LabwareSceneSize, bodyHeight: number, style: SceneObjectStyle): void {
  const railMaterial = new THREE.MeshStandardMaterial({ color: style.accentColor, roughness: 0.44, metalness: 0.16 });
  const railWidth = Math.max(0.08, Math.min(0.18, size.width * 0.035));
  [-0.33, 0.33].forEach((offset) => {
    const rail = new THREE.Mesh(new THREE.BoxGeometry(size.width * 0.92, 0.12, railWidth), railMaterial);
    rail.position.set(0, bodyHeight + 0.08, offset * size.depth);
    group.add(rail);
  });
  for (let index = 0; index < 4; index += 1) {
    const x = (index / 3 - 0.5) * size.width * 0.72;
    const cross = new THREE.Mesh(new THREE.BoxGeometry(railWidth, 0.1, size.depth * 0.72), railMaterial);
    cross.position.set(x, bodyHeight + 0.1, 0);
    group.add(cross);
  }
}

function addNestOrAdapterDetail(group: THREE.Group, size: LabwareSceneSize, bodyHeight: number, style: SceneObjectStyle): void {
  const material = new THREE.MeshStandardMaterial({ color: "#edf1ef", roughness: 0.62, metalness: 0.03 });
  const pad = new THREE.Mesh(new THREE.BoxGeometry(size.width * 0.74, 0.1, size.depth * 0.58), material);
  pad.position.y = bodyHeight + 0.08;
  group.add(pad);

  const ring = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(size.width * 0.78, 0.13, size.depth * 0.62)),
    new THREE.LineBasicMaterial({ color: style.accentColor })
  );
  ring.position.y = bodyHeight + 0.09;
  group.add(ring);
}

function addDeviceDetail(group: THREE.Group, size: LabwareSceneSize, bodyHeight: number, style: SceneObjectStyle): void {
  const material = new THREE.MeshStandardMaterial({ color: style.accentColor, roughness: 0.36, metalness: 0.22 });
  const towerHeight = Math.min(0.72, bodyHeight * 0.72);
  const tower = new THREE.Mesh(new THREE.BoxGeometry(size.width * 0.46, towerHeight, size.depth * 0.42), material);
  tower.position.y = bodyHeight + towerHeight / 2;
  group.add(tower);

  const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 0.08, 24), new THREE.MeshStandardMaterial({ color: "#1f2a2b", roughness: 0.24, metalness: 0.18 }));
  lens.rotation.x = Math.PI / 2;
  lens.position.set(0, bodyHeight + towerHeight * 0.62, -size.depth * 0.22);
  group.add(lens);
}

function addStructuralDetail(group: THREE.Group, size: LabwareSceneSize, bodyHeight: number, style: SceneObjectStyle): void {
  const material = new THREE.MeshStandardMaterial({ color: style.accentColor, roughness: 0.5, metalness: 0.12, transparent: true, opacity: 0.72 });
  const stripeCount = size.width > size.depth ? 4 : 3;
  for (let index = 0; index < stripeCount; index += 1) {
    const x = size.width > size.depth ? (index / Math.max(1, stripeCount - 1) - 0.5) * size.width * 0.72 : 0;
    const z = size.width <= size.depth ? (index / Math.max(1, stripeCount - 1) - 0.5) * size.depth * 0.72 : 0;
    const stripe = new THREE.Mesh(
      new THREE.BoxGeometry(size.width > size.depth ? 0.08 : size.width * 0.72, 0.08, size.width > size.depth ? size.depth * 0.72 : 0.08),
      material
    );
    stripe.position.set(x, bodyHeight + 0.07, z);
    group.add(stripe);
  }
}

function addWasteDetail(group: THREE.Group, size: LabwareSceneSize, bodyHeight: number, style: SceneObjectStyle): void {
  const trough = new THREE.Mesh(
    new THREE.BoxGeometry(Math.max(0.9, size.width * 0.72), 0.22, Math.max(0.35, size.depth * 0.46)),
    new THREE.MeshStandardMaterial({ color: "#e6ecea", roughness: 0.58, metalness: 0.03, transparent: true, opacity: 0.84 })
  );
  trough.position.y = bodyHeight + 0.16;
  group.add(trough);

  const fluid = new THREE.Mesh(
    new THREE.BoxGeometry(Math.max(0.78, size.width * 0.62), 0.045, Math.max(0.25, size.depth * 0.34)),
    new THREE.MeshBasicMaterial({ color: style.accentColor, transparent: true, opacity: 0.36 })
  );
  fluid.position.y = bodyHeight + 0.29;
  group.add(fluid);
}

function addWells(
  group: THREE.Group,
  labware: LabwareModel,
  placement: LabwarePlacement,
  geometry: SceneGeometryContext,
  snapshot: SnapshotModel | undefined,
  activeCommand: CommandModel | undefined
): void {
  const size = placement.size;
  const layout = wellLayoutFor(labware, size);
  const wellVolumes = snapshotLabwareVolumes(snapshot, labware);
  const selected = selectedWellsForLabware(labware, activeCommand);

  labware.wells.forEach((well) => {
    const position = localWellPositionForPlacement(well, labware, placement, geometry, layout);
    const x = position.x;
    const z = position.z;
    const volume = wellVolumes[well.id] ?? well.volumeUl;
    const maxVolumeUl = well.maxVolumeUl || well.capacityUl;
    const isSelected = selected.has(well.id);
    const profile = vesselProfileFor(wellProfileLabware(labware, well), maxVolumeUl, wellRadiusFor(labware, well, size, layout));
    const warning = volumeWarningFor(volume, maxVolumeUl, profile, isSelected, activeCommand);
    addVesselShell(group, x, z, profile, isSelected, warning);

    if (volume > 0) {
      addLiquidVolume(group, x, z, profile, volume, maxVolumeUl, colorForLiquid(well.reagent || activeCommand?.liquidClass || "liquid"), warning);
    }

    if (warning) {
      addVolumeWarning(group, x, z, profile, warning, volume, maxVolumeUl);
    }
  });

  addSelectedWellRectangle(group, labware, placement, geometry, layout, selected);
}

function wellLayoutFor(labware: LabwareModel, size: { width: number; depth: number }): WellLayout {
  const fallback = fallbackLabwareSize(labware);
  const safeWidth = finiteDimension(size.width, fallback.width, 0.9, DECK_WIDTH);
  const safeDepth = finiteDimension(size.depth, fallback.depth, 0.9, DECK_DEPTH);
  const rows = finiteCount(labware.rows);
  const cols = finiteCount(labware.cols);
  const physicalWidthMm = finitePositive(labware.physicalWidthMm) || 0;
  const physicalDepthMm = finitePositive(labware.physicalDepthMm) || 0;
  const columnsAlongDepth = rows === 1 && cols > 1 && physicalDepthMm > physicalWidthMm * 1.5;
  const xMmScale = physicalWidthMm > 0 ? safeWidth / physicalWidthMm : SCENE_MM_SCALE;
  const zMmScale = physicalDepthMm > 0 ? safeDepth / physicalDepthMm : xMmScale;
  const columnScale = columnsAlongDepth ? zMmScale : xMmScale;
  const rowScale = columnsAlongDepth ? xMmScale : zMmScale;
  const fallbackSpanX = Math.max(0.1, safeWidth * 0.78);
  const fallbackSpanZ = Math.max(0.1, safeDepth * 0.72);
  const pitchXMm = finitePositive(labware.pitchXMm) || 0;
  const pitchYMm = finitePositive(labware.pitchYMm) || 0;
  const columnStep = pitchXMm > 0 ? pitchXMm * columnScale : cols > 1 ? fallbackSpanX / (cols - 1) : 0;
  const rowStep = pitchYMm > 0 ? pitchYMm * rowScale : rows > 1 ? fallbackSpanZ / (rows - 1) : 0;
  const spanX = columnsAlongDepth ? Math.max(0, (rows - 1) * rowStep) : Math.max(0, (cols - 1) * columnStep);
  const spanZ = columnsAlongDepth ? Math.max(0, (cols - 1) * columnStep) : Math.max(0, (rows - 1) * rowStep);
  const limitingStep = Math.min(
    positiveOrInfinity(columnStep),
    positiveOrInfinity(rowStep),
    positiveOrInfinity(columnsAlongDepth ? safeWidth * 0.72 : safeDepth * 0.72)
  );
  const diameterRadius = ((finitePositive(labware.wellDiameterMm) || 6) * Math.min(xMmScale, zMmScale)) / 2;
  return {
    spanX: finiteDimension(spanX, fallbackSpanX, 0, DECK_WIDTH),
    spanZ: finiteDimension(spanZ, fallbackSpanZ, 0, DECK_DEPTH),
    stepX: columnsAlongDepth ? rowStep : columnStep,
    stepZ: columnsAlongDepth ? columnStep : rowStep,
    radius: finiteDimension(Math.max(0.045, Math.min(diameterRadius, limitingStep === Infinity ? diameterRadius : limitingStep * 0.44, 0.72)), 0.12, 0.045, 0.72),
    columnsAlongDepth
  };
}

function wellProfileLabware(labware: LabwareModel, well: WellModel): LabwareModel {
  const wellDiameterMm = finitePositive(well.diameterMm) || labware.wellDiameterMm;
  const wellDepthMm = finitePositive(well.depthMm) || labware.wellDepthMm;
  if (wellDiameterMm === labware.wellDiameterMm && wellDepthMm === labware.wellDepthMm) return labware;
  return {
    ...labware,
    wellDiameterMm,
    wellDepthMm
  };
}

function wellRadiusFor(labware: LabwareModel, well: WellModel, size: { width: number; depth: number }, layout: WellLayout): number {
  const diameterMm = finitePositive(well.diameterMm) || finitePositive(labware.wellDiameterMm);
  if (!diameterMm) return layout.radius;

  const fallback = fallbackLabwareSize(labware);
  const safeWidth = finiteDimension(size.width, fallback.width, 0.9, DECK_WIDTH);
  const safeDepth = finiteDimension(size.depth, fallback.depth, 0.9, DECK_DEPTH);
  const physicalWidthMm = finitePositive(labware.physicalWidthMm) || 0;
  const physicalDepthMm = finitePositive(labware.physicalDepthMm) || 0;
  const xMmScale = physicalWidthMm > 0 ? safeWidth / physicalWidthMm : SCENE_MM_SCALE;
  const zMmScale = physicalDepthMm > 0 ? safeDepth / physicalDepthMm : xMmScale;
  const radius = (diameterMm * Math.min(xMmScale, zMmScale)) / 2;
  const limitingStep = Math.min(positiveOrInfinity(layout.stepX), positiveOrInfinity(layout.stepZ));
  const maxByStep = limitingStep === Infinity ? radius : limitingStep * 0.44;
  return finiteDimension(Math.max(0.045, Math.min(radius, maxByStep, 0.72)), layout.radius, 0.045, 0.72);
}

function localWellPosition(
  well: Pick<WellModel, "row" | "col" | "positionMm">,
  labware: LabwareModel,
  size: { width: number; depth: number },
  layout: WellLayout
): THREE.Vector3 {
  const fluentPosition = well.positionMm ? localFluentWellPosition(well.positionMm, labware, size) : null;
  if (fluentPosition) return fluentPosition;

  const x = layout.columnsAlongDepth ? -layout.spanX / 2 + well.row * layout.stepX : -layout.spanX / 2 + well.col * layout.stepX;
  const z = layout.columnsAlongDepth ? -layout.spanZ / 2 + well.col * layout.stepZ : -layout.spanZ / 2 + well.row * layout.stepZ;
  return new THREE.Vector3(x, 0, z);
}

function localFluentWellPosition(positionMm: Vector3Model, labware: LabwareModel, size: { width: number; depth: number }): THREE.Vector3 | null {
  const dimension = labware.transform?.dimensionMm || {
    x: labware.physicalWidthMm,
    y: labware.physicalDepthMm,
    z: labware.physicalHeightMm
  };
  const widthMm = finitePositive(dimension.x) || finitePositive(labware.physicalWidthMm);
  const depthMm = finitePositive(dimension.y) || finitePositive(labware.physicalDepthMm);
  if (!widthMm || !depthMm) return null;

  const fallback = fallbackLabwareSize(labware);
  const safeWidth = finiteDimension(size.width, fallback.width, 0.9, DECK_WIDTH);
  const safeDepth = finiteDimension(size.depth, fallback.depth, 0.9, DECK_DEPTH);
  const xScale = safeWidth / widthMm;
  const zScale = safeDepth / depthMm;
  return new THREE.Vector3((positionMm.x - widthMm / 2) * xScale, 0, -(positionMm.y - depthMm / 2) * zScale);
}

function localWellPositionForPlacement(
  well: Pick<WellModel, "row" | "col" | "positionMm">,
  labware: LabwareModel,
  placement: LabwarePlacement,
  geometry: SceneGeometryContext,
  layout: WellLayout
): THREE.Vector3 {
  const scenePosition = sceneWellPositionForPlacement(well, labware, placement, geometry, layout);
  return scenePosition.sub(placement.position).applyAxisAngle(new THREE.Vector3(0, 1, 0), -placement.rotationY);
}

function sceneWellPositionForPlacement(
  well: Pick<WellModel, "row" | "col" | "positionMm">,
  labware: LabwareModel,
  placement: LabwarePlacement,
  geometry: SceneGeometryContext,
  layout: WellLayout
): THREE.Vector3 {
  const transformed = transformedFluentWellPosition(well.positionMm, labware, geometry);
  if (transformed) return transformed;
  return localWellPosition(well, labware, placement.size, layout).applyAxisAngle(new THREE.Vector3(0, 1, 0), placement.rotationY).add(placement.position);
}

function transformedFluentWellPosition(positionMm: Vector3Model | undefined, labware: LabwareModel, geometry: SceneGeometryContext): THREE.Vector3 | null {
  const transform = labware.transform;
  if (!geometry.hasFluentGeometry || !transform || !positionMm || !isFiniteVector(positionMm)) return null;
  return fluentToThree(addFluentVectors(transform.positionMm, rotateFluentVector(transform.orientation, positionMm)), geometry);
}

function positiveOrInfinity(value: number): number {
  return value > 0 ? value : Infinity;
}

function finitePositive(value: unknown): number | null {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue > 0 ? numberValue : null;
}

function isFiniteVector(vector: Vector3Model): boolean {
  return Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z);
}

function finiteCount(value: unknown): number {
  const numberValue = Math.floor(Number(value));
  return Number.isFinite(numberValue) && numberValue > 0 ? numberValue : 0;
}

function finiteDimension(value: unknown, fallback: number, min: number, max: number): number {
  const numberValue = Number(value);
  const fallbackValue = Number.isFinite(fallback) ? fallback : min;
  const safeValue = Number.isFinite(numberValue) ? numberValue : fallbackValue;
  return Math.max(min, Math.min(max, safeValue));
}

function selectedWellsForLabware(labware: LabwareModel, activeCommand: CommandModel | undefined): Set<string> {
  if (!activeCommand || !labwareLabelMatches(labware.label, activeCommand.targetLabware)) return new Set();
  const validIds = new Set(labware.wells.map((well) => well.id));
  if (activeCommand.wells.length) return new Set(activeCommand.wells.filter((well) => validIds.has(well)));
  if (!commandImplicitlyTargetsAllWells(activeCommand)) return new Set();
  return new Set(labware.wells.map((well) => well.id));
}

function commandImplicitlyTargetsAllWells(activeCommand: CommandModel): boolean {
  if (activeCommand.family === "liquid" || activeCommand.family === "tips" || activeCommand.family === "wash") return true;
  return /aspirate|dispense|mix|wash|detect_liquid|detect liquid|pick_up|get_tips|drop_tips/i.test(activeCommand.operation);
}

function labwareLabelMatches(label: string, targetLabel: string): boolean {
  return label === targetLabel || normalizeLabwareLabel(label) === normalizeLabwareLabel(targetLabel);
}

function normalizeLabwareLabel(value: string): string {
  return value.toLowerCase().replace(/\[[^\]]+\]/g, "").replace(/\s+/g, " ").trim();
}

function snapshotLabwareVolumes(snapshot: SnapshotModel | undefined, labware: LabwareModel): Record<string, number> {
  if (!snapshot) return {};
  const exact = snapshot.labwareVolumes[labware.label];
  if (exact) return exact;
  const normalizedLabel = normalizeLabwareLabel(labware.label);
  const match = Object.entries(snapshot.labwareVolumes).find(([label]) => normalizeLabwareLabel(label) === normalizedLabel);
  return match?.[1] || {};
}

function operationIncludes(activeCommand: CommandModel | undefined, token: string): boolean {
  return Boolean(activeCommand?.operation.toLowerCase().includes(token.toLowerCase()));
}

function placementForLabwareLabel(labwarePlacements: Map<string, LabwarePlacement>, label: string): LabwarePlacement | undefined {
  return labwarePlacements.get(label) || Array.from(labwarePlacements.entries()).find(([candidate]) => labwareLabelMatches(candidate, label))?.[1];
}

function addSelectedWellRectangle(
  group: THREE.Group,
  labware: LabwareModel,
  placement: LabwarePlacement,
  geometry: SceneGeometryContext,
  layout: WellLayout,
  selected: Set<string>
): void {
  if (!selected.size) return;
  const positions = labware.wells
    .filter((well) => selected.has(well.id))
    .map((well) => localWellPositionForPlacement(well, labware, placement, geometry, layout));
  if (!positions.length) return;

  const xs = positions.map((position) => position.x);
  const zs = positions.map((position) => position.z);
  const minX = Math.min(...xs) - layout.radius * 1.45;
  const maxX = Math.max(...xs) + layout.radius * 1.45;
  const minZ = Math.min(...zs) - layout.radius * 1.45;
  const maxZ = Math.max(...zs) + layout.radius * 1.45;
  const width = Math.max(layout.radius * 2.3, maxX - minX);
  const depth = Math.max(layout.radius * 2.3, maxZ - minZ);
  if (!Number.isFinite(width) || !Number.isFinite(depth)) return;
  const edge = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(width, 0.08, depth)),
    new THREE.LineBasicMaterial({ color: "#0f6b73" })
  );
  edge.position.set((minX + maxX) / 2, 0.92, (minZ + maxZ) / 2);
  group.add(edge);
}

function vesselProfileFor(labware: LabwareModel, capacityUl: number, radius: number): VesselProfile {
  const text = `${labware.catalogName} ${labware.label}`.toLowerCase();
  const normalizedCapacity = Math.max(1, capacityUl);

  if (isTubeLabware(labware)) {
    const maxLiquidHeight = Math.min(2.3, Math.max(1.55, (labware.wellDepthMm || 80) * 0.018));
    const tubeRadius = Math.min(0.72, Math.max(0.13, radius * 1.02));
    return {
      kind: "tube",
      footprint: "round",
      radius: tubeRadius,
      width: tubeRadius * 2,
      depth: tubeRadius * 2,
      shellBaseY: 0.5,
      shellHeight: maxLiquidHeight + 0.14,
      liquidBaseY: 0.5,
      maxLiquidHeight,
      coneHeight: maxLiquidHeight * 0.22,
      underfillUl: labware.deadVolumeUl || Math.min(500, Math.max(20, normalizedCapacity * 0.01))
    };
  }

  if (text.includes("wash")) {
    return {
      kind: "reservoir",
      footprint: "square",
      radius,
      width: radius * 1.9,
      depth: radius * 1.55,
      shellBaseY: 0.46,
      shellHeight: 0.56,
      liquidBaseY: 0.5,
      maxLiquidHeight: Math.min(0.65, Math.max(0.35, (labware.wellDepthMm || 30) * 0.014)),
      coneHeight: 0,
      underfillUl: labware.deadVolumeUl || Math.min(50, Math.max(8, normalizedCapacity * 0.015))
    };
  }

  if (text.includes("dwp") || text.includes("deep") || text.includes("filter") || normalizedCapacity >= 1000) {
    const footprint = labware.wellShape === "square" ? "square" : "round";
    return {
      kind: "deepwell",
      footprint,
      radius: radius * 1.04,
      width: radius * 1.86,
      depth: radius * 1.86,
      shellBaseY: 0.46,
      shellHeight: Math.min(0.82, Math.max(0.52, (labware.wellDepthMm || 35) * 0.02)),
      liquidBaseY: 0.5,
      maxLiquidHeight: Math.min(0.76, Math.max(0.45, (labware.wellDepthMm || 35) * 0.018)),
      coneHeight: footprint === "round" ? 0.12 : 0,
      underfillUl: labware.deadVolumeUl || Math.min(80, Math.max(10, normalizedCapacity * 0.015))
    };
  }

  const footprint = labware.wellShape === "square" ? "square" : "round";
  const maxLiquidHeight = Math.min(0.36, Math.max(0.22, (labware.wellDepthMm || 10.8) * 0.028));
  return {
    kind: "plate",
    footprint,
    radius,
    width: radius * 1.8,
    depth: radius * 1.8,
    shellBaseY: 0.46,
    shellHeight: 0.1,
    liquidBaseY: 0.56,
    maxLiquidHeight,
    coneHeight: 0,
    underfillUl: labware.deadVolumeUl || Math.min(8, Math.max(1, normalizedCapacity * 0.02))
  };
}

function addVesselShell(
  group: THREE.Group,
  x: number,
  z: number,
  profile: VesselProfile,
  isSelected: boolean,
  warning: VolumeWarning
): void {
  const color = warning === "overfill" ? "#ffe1de" : warning === "underfill" ? "#fff0bd" : isSelected ? "#f7f1c4" : "#f6f7f5";
  const shellMaterial = new THREE.MeshStandardMaterial({
    color,
    roughness: profile.kind === "tube" ? 0.2 : 0.48,
    metalness: 0.02,
    transparent: profile.kind !== "plate" || Boolean(warning),
    opacity: profile.kind === "tube" ? 0.42 : profile.kind === "plate" ? 0.95 : 0.68,
    side: THREE.DoubleSide
  });

  if (profile.kind === "tube") {
    const cone = new THREE.Mesh(
      new THREE.CylinderGeometry(profile.radius * 0.88, profile.radius * 0.14, profile.coneHeight, 24),
      shellMaterial
    );
    cone.position.set(x, profile.shellBaseY + profile.coneHeight / 2, z);
    cone.castShadow = true;
    group.add(cone);

    const cylinderHeight = profile.shellHeight - profile.coneHeight;
    const cylinder = new THREE.Mesh(new THREE.CylinderGeometry(profile.radius, profile.radius * 0.88, cylinderHeight, 28, 1, true), shellMaterial);
    cylinder.position.set(x, profile.shellBaseY + profile.coneHeight + cylinderHeight / 2, z);
    cylinder.castShadow = true;
    group.add(cylinder);

    const rim = new THREE.Mesh(new THREE.TorusGeometry(profile.radius, 0.018, 8, 28), new THREE.MeshBasicMaterial({ color: warningColor(warning) || "#879492" }));
    rim.rotation.x = Math.PI / 2;
    rim.position.set(x, profile.shellBaseY + profile.shellHeight, z);
    group.add(rim);
    return;
  }

  const geometry =
    profile.footprint === "square"
      ? new THREE.BoxGeometry(profile.width, profile.shellHeight, profile.depth)
      : new THREE.CylinderGeometry(profile.radius, profile.radius, profile.shellHeight, 20);
  const shell = new THREE.Mesh(geometry, shellMaterial);
  shell.position.set(x, profile.shellBaseY + profile.shellHeight / 2, z);
  shell.castShadow = true;
  group.add(shell);
}

function addLiquidVolume(
  group: THREE.Group,
  x: number,
  z: number,
  profile: VesselProfile,
  volumeUl: number,
  capacityUl: number,
  color: string,
  warning: VolumeWarning
): void {
  const liquidHeight = liquidHeightForVolume(volumeUl, capacityUl, profile);
  if (liquidHeight <= 0) return;

  const material = new THREE.MeshStandardMaterial({
    color,
    emissive: warning === "underfill" ? "#c08418" : warning === "overfill" ? "#c43f34" : "#000000",
    emissiveIntensity: warning ? 0.18 : 0,
    transparent: true,
    opacity: warning === "underfill" ? 0.66 : 0.82,
    roughness: 0.32
  });

  if (profile.kind === "tube" && profile.coneHeight > 0) {
    const innerRadius = profile.radius * 0.76;
    if (liquidHeight <= profile.coneHeight) {
      const topRadius = Math.max(0.012, innerRadius * (liquidHeight / profile.coneHeight));
      const liquid = new THREE.Mesh(new THREE.CylinderGeometry(topRadius, 0.008, Math.max(0.012, liquidHeight), 24), material);
      liquid.position.set(x, profile.liquidBaseY + liquidHeight / 2, z);
      group.add(liquid);
    } else {
      const cone = new THREE.Mesh(new THREE.CylinderGeometry(innerRadius, 0.008, profile.coneHeight, 24), material);
      cone.position.set(x, profile.liquidBaseY + profile.coneHeight / 2, z);
      group.add(cone);

      const cylinderHeight = liquidHeight - profile.coneHeight;
      const cylinder = new THREE.Mesh(new THREE.CylinderGeometry(innerRadius, innerRadius, cylinderHeight, 24), material);
      cylinder.position.set(x, profile.liquidBaseY + profile.coneHeight + cylinderHeight / 2, z);
      group.add(cylinder);
    }
  } else {
    const visibleHeight = Math.max(0.012, liquidHeight);
    const geometry =
      profile.footprint === "square"
        ? new THREE.BoxGeometry(profile.width * 0.78, visibleHeight, profile.depth * 0.78)
        : new THREE.CylinderGeometry(profile.radius * 0.78, profile.radius * 0.78, visibleHeight, 18);
    const liquid = new THREE.Mesh(geometry, material);
    liquid.position.set(x, profile.liquidBaseY + visibleHeight / 2, z);
    group.add(liquid);
  }

  if (warning === "overfill") {
    addOverflowCrown(group, x, z, profile);
  }
}

function addOverflowCrown(group: THREE.Group, x: number, z: number, profile: VesselProfile): void {
  const material = new THREE.MeshBasicMaterial({ color: "#d44f44", transparent: true, opacity: 0.46 });
  const height = 0.08;
  const topY = profile.liquidBaseY + profile.maxLiquidHeight + height / 2 + 0.02;
  const geometry =
    profile.footprint === "square"
      ? new THREE.BoxGeometry(profile.width * 1.04, height, profile.depth * 1.04)
      : new THREE.CylinderGeometry(profile.radius * 1.08, profile.radius * 1.08, height, 24);
  const crown = new THREE.Mesh(geometry, material);
  crown.position.set(x, topY, z);
  group.add(crown);
}

function addVolumeWarning(
  group: THREE.Group,
  x: number,
  z: number,
  profile: VesselProfile,
  warning: Exclude<VolumeWarning, null>,
  volumeUl: number,
  capacityUl: number
): void {
  const color = warningColor(warning) || "#d59a35";
  const markerY = profile.shellBaseY + profile.shellHeight + 0.04;
  const ringMaterial = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.88 });
  if (profile.footprint === "square") {
    const edge = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(profile.width * 1.22, 0.08, profile.depth * 1.22)),
      new THREE.LineBasicMaterial({ color })
    );
    edge.position.set(x, markerY, z);
    group.add(edge);
  } else {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(profile.radius * 1.22, 0.022, 8, 24), ringMaterial);
    ring.rotation.x = Math.PI / 2;
    ring.position.set(x, markerY, z);
    group.add(ring);
  }

  const shortVolume = `${Math.round(volumeUl).toLocaleString()}/${Math.round(capacityUl).toLocaleString()} uL`;
  const label = makeTextSprite(warning === "overfill" ? `Over ${shortVolume}` : `Low ${shortVolume}`, color, "rgba(255,248,224,0.92)");
  label.position.set(x, markerY + 0.22, z);
  label.scale.set(profile.kind === "tube" ? 1.25 : 0.9, profile.kind === "tube" ? 0.28 : 0.22, 1);
  group.add(label);
}

function liquidHeightForVolume(volumeUl: number, capacityUl: number, profile: VesselProfile): number {
  const ratio = Math.min(1, Math.max(0, volumeUl) / Math.max(1, capacityUl));
  if (ratio <= 0) return 0;
  if (profile.coneHeight <= 0) return ratio * profile.maxLiquidHeight;

  const cylinderHeight = Math.max(0, profile.maxLiquidHeight - profile.coneHeight);
  const coneVolumeUnits = profile.coneHeight / 3;
  const totalVolumeUnits = coneVolumeUnits + cylinderHeight;
  const coneVolumeFraction = totalVolumeUnits > 0 ? coneVolumeUnits / totalVolumeUnits : 0;
  if (ratio <= coneVolumeFraction) {
    return profile.coneHeight * Math.cbrt(ratio / Math.max(coneVolumeFraction, 0.0001));
  }
  return profile.coneHeight + ((ratio - coneVolumeFraction) / Math.max(0.0001, 1 - coneVolumeFraction)) * cylinderHeight;
}

function volumeWarningFor(
  volumeUl: number,
  capacityUl: number,
  profile: VesselProfile,
  isSelected: boolean,
  activeCommand: CommandModel | undefined
): VolumeWarning {
  if (volumeUl > capacityUl) return "overfill";
  if (volumeUl < 0) return "underfill";
  const dryActiveAspirate = isSelected && operationIncludes(activeCommand, "aspirate") && volumeUl <= 0;
  if (dryActiveAspirate) return "underfill";
  if (volumeUl > 0 && volumeUl < profile.underfillUl) return "underfill";
  return null;
}

function warningColor(warning: VolumeWarning): string {
  if (warning === "overfill") return "#d44f44";
  if (warning === "underfill") return "#d49324";
  return "";
}

function addRobotGantry(
  root: THREE.Group,
  model: ProtocolModel,
  activeCommand: CommandModel | undefined,
  snapshot: SnapshotModel | undefined,
  geometry: SceneGeometryContext,
  labwarePlacements: Map<string, LabwarePlacement>,
  labwareOverrides: LabwareSceneOverrides,
  particles: THREE.Object3D[],
  staticMode: boolean
): void {
  const railMaterial = new THREE.MeshStandardMaterial({ color: "#667273", roughness: 0.46, metalness: 0.14 });
  const crossRail = new THREE.Mesh(new THREE.BoxGeometry(DECK_WIDTH + 1.2, 0.13, 0.13), railMaterial);
  crossRail.position.set(0, 5.2, -DECK_DEPTH / 2 - 1.15);
  root.add(crossRail);

  const headState = headStateFor(model, activeCommand, snapshot, geometry, labwarePlacements, labwareOverrides);
  if (!shouldRenderRobotHead(activeCommand, snapshot, headState)) return;

  const { headPosition } = headState;
  if (headState.kind === "mca") addMcaHead(root, headState);
  else addLihaHead(root, headState);

  const bridge = new THREE.Mesh(new THREE.BoxGeometry(0.16, 3.9, 0.16), railMaterial);
  bridge.position.set(headPosition.x, 3.25, headPosition.z);
  root.add(bridge);

  addTipCones(root, headState);
  addHeadStateLabel(root, headState);

  if (!staticMode && activeCommand?.family === "liquid" && headState.target) {
    addLiquidMotion(root, activeCommand, headState, particles);
  }
}

function addLabwareTransferMotion(
  root: THREE.Group,
  model: ProtocolModel,
  activeCommand: CommandModel | undefined,
  previousSnapshot: SnapshotModel | undefined,
  currentSnapshot: SnapshotModel | undefined,
  geometry: SceneGeometryContext,
  labwarePlacements: Map<string, LabwarePlacement>,
  labwareOverrides: LabwareSceneOverrides,
  particles: THREE.Object3D[],
  staticMode: boolean
): void {
  const transfer = activeCommand?.labwareTransfer;
  if (!transfer) return;
  const labware = targetLabwareForCommand(model.labware, transfer.labware || activeCommand.targetLabware, labwarePlacements);
  if (!labware) return;
  const currentPlacement = labwarePlacements.get(labware.label);
  if (!currentPlacement) return;
  const previousPlacement = transferMotionSourcePlacement(
    labware,
    model.labware,
    previousSnapshot,
    geometry,
    labwarePlacements,
    labwareOverrides
  );
  if (!previousPlacement || previousPlacement.position.distanceToSquared(currentPlacement.position) < 0.02) return;

  const moving = buildTransferGhost(labware, currentPlacement);
  const lift = Math.max(1.8, Math.min(5.2, Math.max(previousPlacement.size.height, currentPlacement.size.height) + 1.6));
  moving.position.copy(staticMode ? transferArcPosition(previousPlacement.position, currentPlacement.position, lift, 0.5) : previousPlacement.position);
  moving.rotation.y = currentPlacement.rotationY;
  moving.userData.mode = "labware-transfer";
  moving.userData.from = previousPlacement.position.clone();
  moving.userData.to = currentPlacement.position.clone();
  moving.userData.lift = lift;
  moving.userData.baseY = moving.position.y;
  root.add(moving);
  particles.push(moving);

  addTransferPath(root, previousPlacement.position, currentPlacement.position, "#f2b134");
  if (staticMode) addTransferEndpointMarkers(root, previousPlacement.position, currentPlacement.position);
}

function transferMotionSourcePlacement(
  labware: LabwareModel,
  labwareItems: LabwareModel[],
  previousSnapshot: SnapshotModel | undefined,
  geometry: SceneGeometryContext,
  labwarePlacements: Map<string, LabwarePlacement>,
  labwareOverrides: LabwareSceneOverrides
): LabwarePlacement | null {
  const previousTransfer = snapshotLabwareTransfer(previousSnapshot, labware);
  if (previousTransfer && !previousTransfer.moveToBase) {
    const previousLabware = labwareWithTransferLocation(labware, previousTransfer);
    const anchor = transferAnchor(previousTransfer, labwareItems, labwarePlacements, labwareOverrides) || protocolTargetAnchor(previousLabware, labwareItems, labwarePlacements);
    if (anchor) return anchoredProtocolTargetPlacement(previousLabware, geometry, anchor);
  }
  if (labware.transform) return labwarePlacement(labware, geometry);
  return labwarePlacements.get(labware.label) || null;
}

function buildTransferGhost(labware: LabwareModel, placement: LabwarePlacement): THREE.Group {
  const group = new THREE.Group();
  group.name = "labware-transfer-ghost";
  const style = sceneObjectStyleFor(labware);
  const size = placement.size;
  const height = Math.max(0.18, Math.min(size.height || 0.4, 1.2));
  const material = new THREE.MeshStandardMaterial({
    color: style.accentColor,
    emissive: style.accentColor,
    emissiveIntensity: 0.18,
    transparent: true,
    opacity: 0.46,
    roughness: 0.44,
    depthWrite: false
  });
  const body = new THREE.Mesh(new THREE.BoxGeometry(Math.max(0.45, size.width), height, Math.max(0.35, size.depth)), material);
  body.position.y = height / 2;
  group.add(body);
  const edge = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(Math.max(0.45, size.width) * 1.02, height * 1.05, Math.max(0.35, size.depth) * 1.02)),
    new THREE.LineBasicMaterial({ color: "#fff2b8", transparent: true, opacity: 0.72 })
  );
  edge.position.copy(body.position);
  group.add(edge);
  return group;
}

function addTransferPath(root: THREE.Group, from: THREE.Vector3, to: THREE.Vector3, color: string): void {
  const mid = from.clone().lerp(to, 0.5);
  mid.y = Math.max(from.y, to.y) + 2.2;
  const curve = new THREE.QuadraticBezierCurve3(from.clone().add(new THREE.Vector3(0, 0.12, 0)), mid, to.clone().add(new THREE.Vector3(0, 0.12, 0)));
  const line = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(curve.getPoints(32)),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.68 })
  );
  line.name = "labware-transfer-path";
  line.raycast = () => undefined;
  root.add(line);
}

function transferArcPosition(from: THREE.Vector3, to: THREE.Vector3, lift: number, progress: number): THREE.Vector3 {
  const phase = clamp(progress, 0, 1);
  const position = from.clone().lerp(to, phase);
  position.y = THREE.MathUtils.lerp(from.y, to.y, phase) + Math.sin(Math.PI * phase) * lift;
  return position;
}

function addTransferEndpointMarkers(root: THREE.Group, from: THREE.Vector3, to: THREE.Vector3): void {
  addTransferEndpointMarker(root, from, "from", "#8aa0ad");
  addTransferEndpointMarker(root, to, "to", "#f2b134");
}

function addTransferEndpointMarker(root: THREE.Group, position: THREE.Vector3, labelText: string, color: string): void {
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.34, 0.42, 32),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.72, side: THREE.DoubleSide, depthWrite: false })
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.set(position.x, Math.max(0.06, position.y + 0.05), position.z);
  ring.raycast = () => undefined;
  root.add(ring);

  const label = makeTextSprite(labelText, "#273434", "rgba(255,255,255,0.78)");
  label.position.set(position.x, ring.position.y + 0.34, position.z);
  label.scale.set(1.1, 0.28, 1);
  root.add(label);
}

function shouldRenderRobotHead(activeCommand: CommandModel | undefined, snapshot: SnapshotModel | undefined, headState: HeadState): boolean {
  if (activeCommand?.labwareTransfer) return false;
  const hasMountedTool = Boolean(snapshot?.tipState.picked || snapshot?.tipState.adapter);
  if (activeCommand && !commandShouldRenderRobotHead(activeCommand)) {
    return hasMountedTool;
  }
  if (activeCommand && commandShouldRenderRobotHead(activeCommand)) return true;
  if (headState.target && hasMountedTool) return true;
  return hasMountedTool;
}

function commandShouldRenderRobotHead(activeCommand: CommandModel): boolean {
  return activeCommand.family === "liquid" || activeCommand.family === "tips" || activeCommand.family === "motion" || activeCommand.family === "wash";
}

function headStateFor(
  model: ProtocolModel,
  activeCommand: CommandModel | undefined,
  snapshot: SnapshotModel | undefined,
  geometry: SceneGeometryContext,
  labwarePlacements: Map<string, LabwarePlacement>,
  labwareOverrides: LabwareSceneOverrides
): HeadState {
  const target = targetSelectionFor(model, activeCommand, labwarePlacements, geometry, labwareOverrides);
  const fallback = model.labware[0] ? labwarePlacements.get(model.labware[0].label)?.position || slotPosition(model.labware[0]) : new THREE.Vector3(0, 0, 0);
  const targetCenter = target?.center || fallback;
  const inferredKind = resolveHeadKind(activeCommand, snapshot, target);
  const channelCount = activeCommand?.channelCount ?? snapshot?.tipState.channelCount ?? defaultChannelCount(inferredKind, target);
  const activeTipCount =
    activeCommand?.activeTipCount ??
    snapshot?.tipState.activeTipCount ??
    (target?.wells.length || defaultChannelCount(inferredKind, target) || 0);

  return {
    kind: inferredKind,
    label: inferredKind === "mca" ? mcaLabel(channelCount) : inferredKind === "liha" ? "LiHa" : "Head",
    channelCount,
    activeTipCount: Math.max(0, activeTipCount),
    selectedTipIndexes: activeCommand?.selectedTipIndexes || [],
    spacingLabel: tipSpacingLabel(activeCommand, snapshot, inferredKind),
    tipsMounted: Boolean(snapshot?.tipState.picked) || commandUsesTips(activeCommand),
    headPosition: new THREE.Vector3(targetCenter.x, HEAD_Y, targetCenter.z),
    target
  };
}

function resolveHeadKind(activeCommand: CommandModel | undefined, snapshot: SnapshotModel | undefined, target: TargetSelection | null): HeadKind {
  if (activeCommand?.headKind && activeCommand.headKind !== "unknown") return activeCommand.headKind;
  if (snapshot?.tipState.headKind && snapshot.tipState.headKind !== "unknown") return snapshot.tipState.headKind;
  if (target?.wells.length && target.wells.length > 8) return "mca";
  if (activeCommand && (activeCommand.family === "liquid" || activeCommand.family === "tips")) return "liha";
  return "unknown";
}

function defaultChannelCount(kind: HeadKind, target: TargetSelection | null): number | null {
  if (kind === "liha") return 8;
  if (kind === "mca") return target?.wells.length && target.wells.length > 96 ? target.wells.length : 96;
  return null;
}

function mcaLabel(channelCount: number | null): string {
  return channelCount ? `MCA${channelCount}` : "MCA";
}

function commandUsesTips(activeCommand: CommandModel | undefined): boolean {
  if (!activeCommand) return false;
  if (activeCommand.family === "liquid") return true;
  return /pick_up|get_tips|set_tips_back|drop_tips/i.test(activeCommand.operation);
}

function tipSpacingLabel(activeCommand: CommandModel | undefined, snapshot: SnapshotModel | undefined, kind: HeadKind): string {
  const x = activeCommand?.tipSpacingXmm ?? snapshot?.tipState.tipSpacingXmm ?? (kind === "mca" ? 9 : null);
  const y = activeCommand?.tipSpacingYmm ?? snapshot?.tipState.tipSpacingYmm ?? (kind === "mca" ? 9 : null);
  if (x !== null && y !== null && Math.abs(x - y) > 0.001) return `${formatMm(x)} x ${formatMm(y)} mm`;
  if (x !== null) return `${formatMm(x)} mm`;
  return "variable";
}

function formatMm(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function targetSelectionFor(
  model: ProtocolModel,
  activeCommand: CommandModel | undefined,
  labwarePlacements: Map<string, LabwarePlacement>,
  geometry: SceneGeometryContext,
  labwareOverrides: LabwareSceneOverrides = new Map()
): TargetSelection | null {
  const transferTarget = transferTargetSelectionFor(model, activeCommand, labwarePlacements, labwareOverrides);
  if (transferTarget) return transferTarget;
  if (!activeCommand?.targetLabware) return null;
  const matchedLabware = targetLabwareForCommand(model.labware, activeCommand.targetLabware, labwarePlacements);
  if (!matchedLabware && isHeadAdapterOperation(activeCommand.operation)) {
    return headAdapterTargetSelectionFor(model.labware, labwarePlacements);
  }
  if (!matchedLabware) return null;
  const labware = labwareOverrides.get(matchedLabware.label) || matchedLabware;
  const placement = labwarePlacements.get(labware.label) || {
    position: slotPosition(labware),
    rotationY: 0,
    size: labwareSize(labware)
  };
  const labwarePosition = placement.position;
  const layout = wellLayoutFor(labware, placement.size);
  const selected = selectedWellsForLabware(labware, activeCommand);
  const wells = labware.wells.filter((well) => selected.has(well.id));
  const positions = wells.map((well) => sceneWellPositionForPlacement(well, labware, placement, geometry, layout));
  const center =
    positions.length > 0
      ? positions.reduce((sum, position) => sum.add(position.clone()), new THREE.Vector3()).divideScalar(positions.length)
      : labwarePosition.clone();
  center.y = Math.max(center.y, 1.05);

  return {
    labware,
    wells,
    positions,
    bounds: selectionBoundsFor(positions, layout.radius * 1.45),
    center
  };
}

function transferTargetSelectionFor(
  model: ProtocolModel,
  activeCommand: CommandModel | undefined,
  labwarePlacements: Map<string, LabwarePlacement>,
  labwareOverrides: LabwareSceneOverrides
): TargetSelection | null {
  const transfer = activeCommand?.labwareTransfer;
  if (!transfer) return null;
  const matchedLabware = targetLabwareForCommand(model.labware, transfer.labware || activeCommand.targetLabware, labwarePlacements);
  if (!matchedLabware) return null;
  const labware = labwareOverrides.get(matchedLabware.label) || matchedLabware;
  const targetPlacement = labwarePlacements.get(labware.label) || labwarePlacements.get(matchedLabware.label);
  if (!targetPlacement) return null;

  const anchor = transferAnchor(transfer, model.labware, labwarePlacements, labwareOverrides);
  const placements = [targetPlacement, anchor?.placement].filter((placement): placement is LabwarePlacement => Boolean(placement));
  const bounds = placementBoundsForCamera(placements, 1.4);
  const center = bounds?.center.clone() || targetPlacement.position.clone();
  center.y = Math.max(center.y, 1.05);

  return {
    labware,
    wells: [],
    positions: placements.map((placement) => placement.position.clone()),
    bounds,
    center
  };
}

function headAdapterTargetSelectionFor(labwareItems: LabwareModel[], labwarePlacements: Map<string, LabwarePlacement>): TargetSelection | null {
  const labware =
    labwareItems.find((item) => labwarePlacements.has(item.label) && sceneObjectStyleFor(item).liquidCapable) ||
    labwareItems.find((item) => labwarePlacements.has(item.label));
  if (!labware) return null;
  const placement = labwarePlacements.get(labware.label);
  if (!placement) return null;
  const bounds = placementBoundsForCamera([placement], 2.2);
  const center = placement.position.clone();
  center.y = Math.max(center.y, 1.05);
  return {
    labware,
    wells: [],
    positions: [placement.position.clone()],
    bounds,
    center
  };
}

function isHeadAdapterOperation(operation: string): boolean {
  return /(?:^|_)get_head_adapter$|(?:^|_)drop_head_adapter$|mount_adapter|drop_adapter/i.test(operation);
}

function targetLabwareForCommand(
  labwareItems: LabwareModel[],
  targetLabel: string,
  labwarePlacements: Map<string, LabwarePlacement>
): LabwareModel | undefined {
  const matches = labwareItems.filter((item) => labwareLabelMatches(item.label, targetLabel));
  if (!matches.length) return undefined;

  const placedMatches = matches.filter((item) => labwarePlacements.has(item.label));
  return (
    placedMatches.find((item) => item.transform) ||
    placedMatches[0] ||
    matches.find((item) => item.transform && sceneObjectStyleFor(item).kind !== "structural") ||
    matches.find((item) => item.transform) ||
    matches[0]
  );
}

function frameCameraForActiveTarget(
  runtime: RuntimeScene,
  target: TargetSelection | null,
  labwarePlacements: Map<string, LabwarePlacement>
): boolean {
  const targetPlacement = target ? labwarePlacements.get(target.labware.label) : undefined;
  if (!target || !targetPlacement) return false;
  const bounds = target.bounds;
  const footprint = bounds ? Math.max(bounds.width, bounds.depth, 2.4) : Math.max(targetPlacement.size.width, targetPlacement.size.depth, 2.4);
  const distance = clamp(footprint * 4.4, 17, 34);
  const center = target.center.clone();
  const lookAt = new THREE.Vector3(center.x, 0.62, center.z);
  runtime.controls.target.copy(lookAt);
  runtime.camera.position.set(center.x, lookAt.y + distance * 0.66, center.z + distance * 0.92);
  runtime.camera.lookAt(lookAt);
  runtime.camera.updateProjectionMatrix();
  runtime.controls.update();
  return true;
}

// Direction the overview camera sits relative to the table center: pulled left,
// raised, and toward the viewer for an elevated 3/4 view matching FluentControl's
// default worktable framing.
const SCENE_OVERVIEW_VIEW_DIR = new THREE.Vector3(-0.37, 0.48, 0.8).normalize();

function frameCameraForSceneOverview(runtime: RuntimeScene, labwarePlacements: Map<string, LabwarePlacement>): void {
  const frame = sceneOverviewFrame(runtime, labwarePlacements);
  if (!frame) {
    const lookAt = new THREE.Vector3(0, 0.35, 0);
    runtime.controls.target.copy(lookAt);
    runtime.camera.position.set(0, 24, 28);
    runtime.camera.lookAt(lookAt);
    runtime.controls.update();
    return;
  }

  const { center, spanX, spanY, spanZ } = frame;
  const aspect = Math.max(0.35, runtime.camera.aspect || 1);
  const canvasWidth =
    runtime.renderer.domElement.clientWidth || runtime.renderer.domElement.width / Math.max(1, runtime.renderer.getPixelRatio());
  const compactCanvas = canvasWidth < 520 && aspect < 0.9;
  const effectiveAspect = compactCanvas ? Math.max(aspect, 0.92) : aspect;

  // Distance that frames the full footprint: depth governs directly, width is
  // divided by aspect (wide viewports fit more horizontally), and the table's
  // vertical extent (deck + cabinet) is folded in so the cabinet is never clipped.
  const fitSpan = Math.max(spanZ, spanX / effectiveAspect, spanY * 0.85);
  const distance = clamp(fitSpan * (compactCanvas ? 1.7 : 1.5), 24, 120);

  // Keep the table out of the depth fog and inside the far plane at this distance.
  runtime.camera.far = Math.max(140, distance * 3.5);
  if (runtime.scene.fog instanceof THREE.Fog) {
    runtime.scene.fog.near = Math.max(90, distance * 1.5);
    runtime.scene.fog.far = distance * 3.2 + 140;
  }
  // Allow OrbitControls to actually sit at the overview distance (and zoom a bit
  // past it) instead of clamping the camera back in and re-clipping the table.
  runtime.controls.maxDistance = Math.max(runtime.controls.maxDistance, distance * 1.4);

  runtime.controls.target.copy(center);
  runtime.camera.position.copy(center).addScaledVector(SCENE_OVERVIEW_VIEW_DIR, distance);
  runtime.camera.lookAt(center);
  runtime.camera.updateProjectionMatrix();
  runtime.controls.update();
}

// Computes the worktable framing target from the true world-space bounds of the
// built scene (deck + cabinet + rulers + labware). Falls back to labware-footprint
// bounds when the root is empty (e.g. before any geometry is added).
function sceneOverviewFrame(
  runtime: RuntimeScene,
  labwarePlacements: Map<string, LabwarePlacement>
): { center: THREE.Vector3; spanX: number; spanY: number; spanZ: number } | null {
  runtime.root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(runtime.root);
  if (!box.isEmpty() && Number.isFinite(box.min.x) && Number.isFinite(box.max.x)) {
    const size = box.getSize(new THREE.Vector3());
    return {
      center: box.getCenter(new THREE.Vector3()),
      spanX: Math.max(8, size.x),
      spanY: Math.max(2, size.y),
      spanZ: Math.max(8, size.z),
    };
  }

  const bounds = scenePlacementBounds(labwarePlacements);
  if (!bounds) return null;
  return {
    center: new THREE.Vector3((bounds.minX + bounds.maxX) / 2, 0.3, (bounds.minZ + bounds.maxZ) / 2),
    spanX: Math.max(8, bounds.maxX - bounds.minX),
    spanY: 6,
    spanZ: Math.max(8, bounds.maxZ - bounds.minZ),
  };
}

function scenePlacementBounds(labwarePlacements: Map<string, LabwarePlacement>): SelectionBounds | null {
  return placementBoundsForCamera(Array.from(labwarePlacements.values()));
}

function placementBoundsForCamera(placements: LabwarePlacement[], padding = 0): SelectionBounds | null {
  if (!placements.length) return null;
  const xs: number[] = [];
  const zs: number[] = [];

  placements.forEach((placement) => {
    const halfWidth = placement.size.width / 2;
    const halfDepth = placement.size.depth / 2;
    xs.push(placement.position.x - halfWidth, placement.position.x + halfWidth);
    zs.push(placement.position.z - halfDepth, placement.position.z + halfDepth);
  });

  const minX = Math.min(...xs) - padding;
  const maxX = Math.max(...xs) + padding;
  const minZ = Math.min(...zs) - padding;
  const maxZ = Math.max(...zs) + padding;
  return {
    minX,
    maxX,
    minZ,
    maxZ,
    width: maxX - minX,
    depth: maxZ - minZ,
    center: new THREE.Vector3((minX + maxX) / 2, 0, (minZ + maxZ) / 2)
  };
}

function selectionBoundsFor(positions: THREE.Vector3[], padding = 0): SelectionBounds | null {
  if (!positions.length) return null;
  const xs = positions.map((position) => position.x);
  const zs = positions.map((position) => position.z);
  const minX = Math.min(...xs) - padding;
  const maxX = Math.max(...xs) + padding;
  const minZ = Math.min(...zs) - padding;
  const maxZ = Math.max(...zs) + padding;
  return {
    minX,
    maxX,
    minZ,
    maxZ,
    width: maxX - minX,
    depth: maxZ - minZ,
    center: new THREE.Vector3((minX + maxX) / 2, 0, (minZ + maxZ) / 2)
  };
}

function addLihaHead(root: THREE.Group, headState: HeadState): void {
  const targetWidth = headState.target?.bounds?.width ?? 1.8;
  const width = clamp(Math.max(targetWidth + 0.9, headState.activeTipCount * 0.32 + 0.9), 2.5, 5.8);
  const carriage = new THREE.Mesh(
    new THREE.BoxGeometry(width, 0.42, 0.66),
    new THREE.MeshStandardMaterial({ color: "#3e494a", roughness: 0.42, metalness: 0.28 })
  );
  carriage.position.copy(headState.headPosition);
  carriage.castShadow = true;
  root.add(carriage);

  const face = new THREE.Mesh(
    new THREE.BoxGeometry(width * 0.86, 0.08, 0.46),
    new THREE.MeshStandardMaterial({ color: "#607173", roughness: 0.35, metalness: 0.18 })
  );
  face.position.set(headState.headPosition.x, headState.headPosition.y - 0.34, headState.headPosition.z);
  root.add(face);

  const channelCount = Math.max(1, Math.min(8, headState.channelCount || 8));
  const activeSet = new Set(headState.selectedTipIndexes);
  for (let index = 0; index < channelCount; index += 1) {
    const offset = channelCount === 1 ? 0 : (index / (channelCount - 1) - 0.5) * (width - 0.48);
    const active = activeSet.size ? activeSet.has(index) : index < headState.activeTipCount;
    const channel = new THREE.Mesh(
      new THREE.BoxGeometry(0.11, active ? 0.72 : 0.46, 0.18),
      new THREE.MeshStandardMaterial({ color: active ? "#dcebea" : "#899595", roughness: 0.34, metalness: 0.12 })
    );
    channel.position.set(headState.headPosition.x + offset, headState.headPosition.y + (active ? -0.45 : -0.33), headState.headPosition.z);
    root.add(channel);
  }
}

function addMcaHead(root: THREE.Group, headState: HeadState): void {
  const targetWidth = headState.target?.bounds?.width ?? 5.2;
  const targetDepth = headState.target?.bounds?.depth ?? 3.2;
  const width = clamp(targetWidth + 0.82, 2.9, 7.1);
  const depth = clamp(targetDepth + 0.72, 1.7, 4.9);
  const carriage = new THREE.Mesh(
    new THREE.BoxGeometry(width, 0.62, depth),
    new THREE.MeshStandardMaterial({ color: "#425052", roughness: 0.46, metalness: 0.18 })
  );
  carriage.position.copy(headState.headPosition);
  carriage.castShadow = true;
  root.add(carriage);

  const lowerPlate = new THREE.Mesh(
    new THREE.BoxGeometry(width * 0.92, 0.16, depth * 0.9),
    new THREE.MeshStandardMaterial({ color: "#7a8987", roughness: 0.38, metalness: 0.12 })
  );
  lowerPlate.position.set(headState.headPosition.x, headState.headPosition.y - 0.46, headState.headPosition.z);
  root.add(lowerPlate);

  addMcaNozzleGrid(root, headState);
}

function addMcaNozzleGrid(root: THREE.Group, headState: HeadState): void {
  const positions = tipPositionsForHead(headState).slice(0, MAX_RENDERED_TIPS);
  if (!positions.length) return;
  const activeSet = new Set(headState.selectedTipIndexes);
  const activeLimit = headState.activeTipCount || positions.length;
  const activeTopPortMaterial = new THREE.MeshBasicMaterial({ color: "#eef8f6" });
  const idleTopPortMaterial = new THREE.MeshBasicMaterial({ color: "#8c9a98" });
  const activePortMaterial = new THREE.MeshStandardMaterial({ color: "#f5fbfa", roughness: 0.3, metalness: 0.04 });
  const idlePortMaterial = new THREE.MeshStandardMaterial({ color: "#c8d4d2", roughness: 0.38, metalness: 0.04 });
  const activeBarrelMaterial = new THREE.MeshStandardMaterial({
    color: "#e7f2f0",
    roughness: 0.32,
    metalness: 0.04,
    transparent: true,
    opacity: 0.92
  });
  const idleBarrelMaterial = new THREE.MeshStandardMaterial({
    color: "#aebbb9",
    roughness: 0.36,
    metalness: 0.04,
    transparent: true,
    opacity: 0.54
  });

  positions.forEach((position, index) => {
    const active = activeSet.size ? activeSet.has(index) : index < activeLimit;
    const topPort = new THREE.Mesh(new THREE.CylinderGeometry(0.047, 0.047, 0.03, 12), active ? activeTopPortMaterial : idleTopPortMaterial);
    topPort.position.set(position.x, headState.headPosition.y + 0.325, position.z);
    root.add(topPort);

    const port = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.055, 0.04, 12), active ? activePortMaterial : idlePortMaterial);
    port.position.set(position.x, headState.headPosition.y - 0.58, position.z);
    root.add(port);

    const barrelHeight = active ? 0.66 : 0.42;
    const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.026, 0.038, barrelHeight, 10), active ? activeBarrelMaterial : idleBarrelMaterial);
    barrel.position.set(position.x, headState.headPosition.y - 0.61 - barrelHeight / 2, position.z);
    root.add(barrel);
  });
}

function addTipCones(root: THREE.Group, headState: HeadState): void {
  const positions = tipPositionsForHead(headState);
  const tipMaterial = new THREE.MeshStandardMaterial({
    color: headState.tipsMounted ? "#f4f7f6" : "#b9c0bf",
    roughness: 0.36,
    metalness: 0.04
  });
  const tipHeight = headState.tipsMounted ? (headState.kind === "mca" ? 1.28 : 1.42) : 0.52;
  const radius = headState.kind === "mca" ? 0.056 : 0.066;

  positions.slice(0, MAX_RENDERED_TIPS).forEach((position) => {
    const tip = new THREE.Mesh(new THREE.ConeGeometry(radius, tipHeight, 12), tipMaterial);
    tip.position.set(position.x, headState.headPosition.y - tipHeight / 2 - 0.5, position.z);
    tip.rotation.x = Math.PI;
    root.add(tip);
  });
}

function tipPositionsForHead(headState: HeadState): THREE.Vector3[] {
  if (headState.target?.positions.length) {
    const limit = headState.kind === "liha" && headState.activeTipCount ? headState.activeTipCount : headState.target.positions.length;
    return headState.target.positions.slice(0, limit);
  }

  const count = Math.max(1, Math.min(headState.kind === "mca" ? 12 : 8, headState.activeTipCount || headState.channelCount || 1));
  const spacing = headState.kind === "mca" ? 0.32 : 0.28;
  return Array.from({ length: count }, (_, index) => {
    const offset = (index - (count - 1) / 2) * spacing;
    return new THREE.Vector3(headState.headPosition.x + offset, 1.05, headState.headPosition.z);
  });
}

function addHeadStateLabel(root: THREE.Group, headState: HeadState): void {
  const channels = headState.channelCount === null ? "unknown ch" : `${headState.channelCount} ch`;
  const activeTips = `${headState.activeTipCount || 0} tips`;
  const detail = `${headState.label} | ${channels} | ${activeTips} | ${headState.spacingLabel}`;
  const tipsExceedChannels =
    headState.channelCount !== null && headState.activeTipCount > headState.channelCount;
  const labelColor = tipsExceedChannels ? "#ffffff" : "#253130";
  const labelBackground = tipsExceedChannels ? "rgba(176,38,28,0.94)" : "rgba(255,255,255,0.86)";
  const label = makeTextSprite(detail, labelColor, labelBackground);
  label.position.set(headState.headPosition.x, headState.headPosition.y + 0.72, headState.headPosition.z - 0.18);
  label.scale.set(4.5, 0.52, 1);
  root.add(label);

  if (tipsExceedChannels) {
    const warning = makeTextSprite(
      `! ${headState.activeTipCount} tips > ${headState.channelCount} channels`,
      "#ffffff",
      "rgba(176,38,28,0.94)"
    );
    warning.position.set(headState.headPosition.x, headState.headPosition.y + 1.32, headState.headPosition.z - 0.18);
    warning.scale.set(4.2, 0.52, 1);
    warning.renderOrder = 6;
    root.add(warning);
  }
}

function addLiquidMotion(root: THREE.Group, activeCommand: CommandModel, headState: HeadState, particles: THREE.Object3D[]): void {
  if (!headState.target) return;
  const liquidColor = colorForLiquid(activeCommand.liquidClass || "liquid");
  const positions = headState.target.positions.length ? headState.target.positions : [headState.target.center];
  const mode = operationIncludes(activeCommand, "aspirate") ? "aspirate" : "dispense";
  positions.slice(0, MAX_LIQUID_PARTICLES).forEach((targetPosition, index) => {
    const particle = new THREE.Mesh(
      new THREE.SphereGeometry(0.08, 18, 12),
      new THREE.MeshStandardMaterial({
        color: liquidColor,
        emissive: liquidColor,
        emissiveIntensity: 0.22,
        transparent: true,
        opacity: 0.84
      })
    );
    particle.position.set(targetPosition.x, 1.1, targetPosition.z);
    particle.userData.baseY = Math.max(0.92, targetPosition.y + 0.42 + (index % 3) * 0.04);
    particle.userData.mode = mode;
    root.add(particle);
    particles.push(particle);
  });

  positions.slice(0, MAX_LIQUID_STREAMS).forEach((targetPosition) => {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(targetPosition.x, 0.85, targetPosition.z),
      new THREE.Vector3((targetPosition.x + headState.headPosition.x) / 2, 2.6, (targetPosition.z + headState.headPosition.z) / 2),
      new THREE.Vector3(headState.headPosition.x, headState.headPosition.y - 0.65, headState.headPosition.z)
    ]);
    const tube = new THREE.Mesh(
      new THREE.TubeGeometry(curve, 24, 0.016, 8, false),
      new THREE.MeshBasicMaterial({ color: liquidColor, transparent: true, opacity: 0.38 })
    );
    root.add(tube);
  });
}

function geometryDebugEnabled(): boolean {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  if (DEBUG_QUERY_KEYS.some((key) => truthyQueryValue(params.get(key)))) return true;
  return window.localStorage?.getItem("deckGeometryDebug") === "true";
}

function truthyQueryValue(value: string | null): boolean {
  return value === "1" || value === "true" || value === "yes" || value === "geometry";
}

function addFluentGeometryDebug(
  root: THREE.Group,
  labwareItems: LabwareModel[],
  geometry: SceneGeometryContext,
  labwarePlacements: Map<string, LabwarePlacement>
): void {
  const debugGroup = new THREE.Group();
  debugGroup.name = "fluent-geometry-debug";
  root.add(debugGroup);

  labwareItems
    .filter((labware) => labware.transform)
    .slice(0, MAX_DEBUG_OBJECTS)
    .forEach((labware) => {
      const placement = labwarePlacements.get(labware.label);
      if (!placement || !labware.transform) return;
      const style = sceneObjectStyleFor(labware);
      const size = placement.size;
      addDebugBoundingBox(debugGroup, placement, size, style.accentColor);
      addDebugOriginAndAxes(debugGroup, labware, geometry, style);
    });
}

function addDebugBoundingBox(root: THREE.Group, placement: LabwarePlacement, size: LabwareSceneSize, color: string): void {
  const box = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(size.width, Math.max(0.08, size.height), size.depth)),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.78 })
  );
  box.position.copy(placement.position);
  box.position.y += Math.max(0.08, size.height) / 2 + 0.025;
  box.rotation.y = placement.rotationY;
  root.add(box);
}

function addDebugOriginAndAxes(root: THREE.Group, labware: LabwareModel, geometry: SceneGeometryContext, style: SceneObjectStyle): void {
  const transform = labware.transform;
  if (!transform) return;

  const origin = fluentToThree(transform.positionMm, geometry);
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry(0.085, 12, 8),
    new THREE.MeshBasicMaterial({ color: style.accentColor, transparent: true, opacity: 0.88 })
  );
  marker.position.copy(origin);
  marker.position.y = Math.max(marker.position.y, 0.18);
  root.add(marker);

  const meshLabel = debugMeshLabelFor(labware);
  if (meshLabel) {
    const label = makeTextSprite(meshLabel, style.accentColor, "rgba(255,255,255,0.72)");
    label.position.copy(marker.position);
    label.position.y += 0.32;
    label.scale.set(2.8, 0.38, 1);
    root.add(label);
  }

  const axisLengthMm = Math.max(30, Math.min(120, Math.max(labware.physicalWidthMm, labware.physicalDepthMm) * 0.3));
  const xAxis = fluentToThree(addFluentVectors(transform.positionMm, rotateFluentVector(transform.orientation, { x: axisLengthMm, y: 0, z: 0 })), geometry);
  const yAxis = fluentToThree(addFluentVectors(transform.positionMm, rotateFluentVector(transform.orientation, { x: 0, y: axisLengthMm, z: 0 })), geometry);
  const zAxis = fluentToThree(addFluentVectors(transform.positionMm, rotateFluentVector(transform.orientation, { x: 0, y: 0, z: axisLengthMm })), geometry);
  addDebugLine(root, marker.position, withMinimumDebugY(xAxis), "#d44f44");
  addDebugLine(root, marker.position, withMinimumDebugY(yAxis), "#4f8f63");
  addDebugLine(root, marker.position, withMinimumDebugY(zAxis), "#4e79a7");
}


function debugMeshLabelFor(labware: LabwareModel): string {
  const hints = labware as LabwareSceneHints;
  const meshName = hints.meshName || (labware.transform as TransformSceneHints | null | undefined)?.meshName || "";
  const meshGuid = sanitizeGuid(hints.meshGuid || (labware.transform as TransformSceneHints | null | undefined)?.meshGuid || "");
  const assetPath = modelAssetPathForLabware(labware);
  if (meshName && meshGuid) return `${meshName} | ${meshGuid.slice(0, 8)}`;
  if (meshName) return meshName;
  if (meshGuid) return meshGuid.slice(0, 8);
  return assetPath ? pathBasename(assetPath) : "";
}

function withMinimumDebugY(value: THREE.Vector3): THREE.Vector3 {
  return new THREE.Vector3(value.x, Math.max(value.y, 0.18), value.z);
}

function addDebugLine(root: THREE.Group, start: THREE.Vector3, end: THREE.Vector3, color: string): void {
  const geometry = new THREE.BufferGeometry().setFromPoints([start, end]);
  const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 }));
  root.add(line);
}

function addDiffMarkers(root: THREE.Group, model: ProtocolModel, labwarePlacements: Map<string, LabwarePlacement>): void {
  model.repairs.issues
    .filter((issue) => issue.kind === "missing_labware" || issue.kind === "changed_position" || issue.kind === "worktable")
    .slice(0, 18)
    .forEach((issue, index) => {
      const slot = slotFromIssue(issue.targetValue) ?? slotFromIssue(issue.sourceValue) ?? index;
      const placement = labwarePlacements.get(issue.label);
      const x = placement?.position.x ?? -DECK_WIDTH / 2 + 3.8 + (slot % SLOT_COLUMNS) * SLOT_WIDTH;
      const z = placement?.position.z ?? -DECK_DEPTH / 2 + 2.6 + Math.floor(slot / SLOT_COLUMNS) * SLOT_DEPTH;
      const color = issue.severity === "blocking" ? "#d45c4c" : issue.severity === "needs_review" ? "#d59a35" : "#4f8f63";
      const marker = new THREE.Mesh(
        new THREE.BoxGeometry(5.7, 0.16, 3.55),
        new THREE.MeshStandardMaterial({
          color,
          emissive: color,
          emissiveIntensity: 0.18,
          transparent: true,
          opacity: 0.34,
          roughness: 0.5
        })
      );
      marker.position.set(x, 0.78, z);
      marker.castShadow = true;
      root.add(marker);

      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(5.72, 0.18, 3.57)),
        new THREE.LineBasicMaterial({ color })
      );
      edge.position.copy(marker.position);
      root.add(edge);

      const label = makeTextSprite(`${issue.kind.replace("_", " ")}: ${issue.label}`, "#3b2a1e", "rgba(255,249,229,0.9)");
      label.position.set(x, 1.55, z);
      label.scale.set(3.9, 0.5, 1);
      root.add(label);
    });
}

function addFocusMarker(
  root: THREE.Group,
  model: ProtocolModel,
  focusTarget: SceneFocusTarget | null,
  labwarePlacements: Map<string, LabwarePlacement>,
  hasFluentGeometry: boolean
): void {
  if (!focusTarget) return;
  const targetLabel = focusTarget.labwareLabel || "";
  const targetLabware = targetLabel ? model.labware.find((labware) => labwareLabelMatches(labware.label, targetLabel)) : undefined;
  const targetPlacement = targetLabel ? placementForLabwareLabel(labwarePlacements, targetLabel) : undefined;
  const position =
    targetPlacement?.position ??
    (typeof focusTarget.slotIndex === "number" ? slotPositionFromIndex(focusTarget.slotIndex) : undefined) ??
    (targetLabware ? labwarePlacements.get(targetLabware.label)?.position || slotPosition(targetLabware) : undefined);
  if (!position) return;

  const color = focusTarget.kind === "liquid" ? "#19747b" : focusTarget.kind === "site" ? "#6f659e" : "#b77921";
  const markerWidth = clamp((targetPlacement?.size.width ?? 5.7) + 0.32, 1.2, 6.4);
  const markerDepth = clamp((targetPlacement?.size.depth ?? 3.7) + 0.32, 0.9, 4.2);
  const markerHeight = 0.14;
  const markerY = Math.max(0.28, Math.min(1.05, position.y + 0.08));
  const marker = new THREE.Mesh(
    new THREE.BoxGeometry(markerWidth, markerHeight, markerDepth),
    new THREE.MeshStandardMaterial({
      color,
      emissive: color,
      emissiveIntensity: 0.24,
      transparent: true,
      opacity: 0.32,
      roughness: 0.42
    })
  );
  marker.position.set(position.x, markerY, position.z);
  root.add(marker);

  const edge = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(markerWidth + 0.08, markerHeight + 0.04, markerDepth + 0.08)),
    new THREE.LineBasicMaterial({ color })
  );
  edge.position.copy(marker.position);
  root.add(edge);

  if (!hasFluentGeometry || geometryDebugEnabled()) {
    const label = makeTextSprite(`Focus: ${focusTarget.label}`, "#253130", "rgba(255,255,255,0.9)");
    label.position.set(position.x, 2.05, position.z + 1.95);
    label.scale.set(3.8, 0.5, 1);
    root.add(label);
  }
}

function slotFromIssue(value: string): number | null {
  const match = value.match(/(?:^|\s)(\d+)(?:\s*)$/);
  if (!match) return null;
  const slot = Number(match[1]);
  return Number.isFinite(slot) && slot > 0 ? slot - 1 : null;
}

function labwareSize(labware: LabwareModel, geometry?: SceneGeometryContext, forceFluentScale = false): LabwareSceneSize {
  const fallback = fallbackLabwareSize(labware);
  const transformDimension = geometry?.hasFluentGeometry ? labware.transform?.dimensionMm : null;
  const scale = geometry?.hasFluentGeometry && (labware.transform || forceFluentScale) ? geometry.scale : SCENE_MM_SCALE;
  const physicalWidthMm = finitePositive(transformDimension?.x) || finitePositive(labware.physicalWidthMm);
  const physicalDepthMm = finitePositive(transformDimension?.y) || finitePositive(labware.physicalDepthMm);
  const physicalHeightMm = finitePositive(transformDimension?.z) || finitePositive(labware.physicalHeightMm);
  const width = physicalWidthMm ? physicalWidthMm * scale : fallback.width;
  const depth = physicalDepthMm ? physicalDepthMm * scale : fallback.depth;
  const height = physicalHeightMm ? physicalHeightMm * scale : 0.42;
  return {
    width: finiteDimension(width, fallback.width, 0.9, DECK_WIDTH),
    depth: finiteDimension(depth, fallback.depth, 0.9, DECK_DEPTH - 0.4),
    height: finiteDimension(height, 0.42, 0.12, MAX_BODY_HEIGHT)
  };
}

function fallbackLabwareSize(labware: LabwareModel): { width: number; depth: number } {
  const text = `${labware.catalogName} ${labware.label}`.toLowerCase();
  if (labware.hardwareProfile === "tube-holder") return labware.cols <= 1 ? { width: 1.55, depth: 1.55 } : { width: 6.28, depth: 1.5 };
  if (labware.hardwareProfile === "cap-holder") return { width: 6.15, depth: 1.24 };
  if (labware.hardwareProfile === "24-filter-plate") return { width: 5.86, depth: 4.1 };
  if (labware.hardwareProfile === "24-dwp") return { width: 5.74, depth: 3.86 };
  if (isTubeLabware(labware)) return labware.cols <= 1 ? { width: 1.55, depth: 1.55 } : { width: 6.1, depth: 1.55 };
  if (text.includes("runner") || labware.rows === 1) return { width: 6.1, depth: 1.55 };
  if (text.includes("wash")) return { width: 5.4, depth: 1.65 };
  if (labware.rows === 0 || labware.cols === 0) return { width: 4.9, depth: 2.7 };
  if (labware.rows >= 16 || labware.cols >= 24) return { width: 6.4, depth: 4.2 };
  if (labware.rows === 4 && labware.cols === 6) return { width: 5.7, depth: 3.7 };
  return { width: 6.0, depth: 3.9 };
}

function labwareLabelY(labware: LabwareModel, size?: LabwareSceneSize): number {
  if (size && size.height > 0.7) return size.height + 0.52;
  if (labware.hardwareProfile === "cap-holder") return 1.36;
  if (isTubeLabware(labware)) return labware.capacityUl >= 50000 ? 3.08 : 2.78;
  const text = `${labware.catalogName} ${labware.label}`.toLowerCase();
  if (text.includes("dwp") || text.includes("deep") || text.includes("filter") || labware.capacityUl >= 1000) return 1.55;
  return 1.1;
}

function isTubeLabware(labware: LabwareModel): boolean {
  const text = `${labware.catalogName} ${labware.label}`.toLowerCase();
  if (labware.hardwareProfile === "cap-holder" || (text.includes("cap") && (text.includes("holder") || text.includes("no tubes")))) return false;
  if (labware.hardwareProfile === "tube-holder") return labware.capacityUl >= 1000 && labware.wellShape === "round";
  return (text.includes("tube holder") || text.includes("tube runner")) && labware.wellShape === "round" && labware.capacityUl >= 1000;
}

function slotPosition(labware: LabwareModel): THREE.Vector3 {
  const slot = Math.max(0, labware.position ? labware.position - 1 : labware.slotIndex);
  return slotPositionFromIndex(slot);
}

function slotPositionFromIndex(slotIndex: number): THREE.Vector3 {
  const slot = Math.max(0, slotIndex);
  const x = -DECK_WIDTH / 2 + 3.8 + (slot % SLOT_COLUMNS) * SLOT_WIDTH;
  const z = -DECK_DEPTH / 2 + 2.6 + Math.floor(slot / SLOT_COLUMNS) * SLOT_DEPTH;
  return new THREE.Vector3(x, 0, z);
}

function colorForLiquid(seed: string): string {
  const text = seed || "liquid";
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) % LIQUID_COLORS.length;
  }
  return LIQUID_COLORS[Math.abs(hash) % LIQUID_COLORS.length];
}

function makeTextSprite(text: string, color: string, background: string): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 96;
  const context = canvas.getContext("2d");
  if (context) {
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = background;
    roundRect(context, 8, 12, canvas.width - 16, canvas.height - 24, 14);
    context.fill();
    context.fillStyle = color;
    context.font = "600 30px Inter, system-ui, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(truncate(text, 30), canvas.width / 2, canvas.height / 2 + 1);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.anisotropy = 4;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true });
  return new THREE.Sprite(material);
}

function roundRect(context: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number): void {
  context.beginPath();
  context.moveTo(x + radius, y);
  context.lineTo(x + width - radius, y);
  context.quadraticCurveTo(x + width, y, x + width, y + radius);
  context.lineTo(x + width, y + height - radius);
  context.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  context.lineTo(x + radius, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - radius);
  context.lineTo(x, y + radius);
  context.quadraticCurveTo(x, y, x + radius, y);
  context.closePath();
}

function truncate(text: string, maxLength: number): string {
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

function resizeRenderer(container: HTMLElement, runtime: RuntimeScene): void {
  const width = Math.max(320, container.clientWidth);
  const height = Math.max(280, container.clientHeight);
  runtime.camera.aspect = width / height;
  runtime.camera.updateProjectionMatrix();
  runtime.renderer.setSize(width, height, false);
}

function disposeGroup(group: THREE.Group): void {
  // Dedupe shared geometries/materials so a resource reused across sibling meshes
  // (tip racks, MCA nozzles, deck rails, etc.) is only released once.
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  group.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (mesh.geometry) geometries.add(mesh.geometry);
    const material = mesh.material;
    if (Array.isArray(material)) material.forEach((item) => materials.add(item));
    else if (material) materials.add(material);
  });
  geometries.forEach((geometry) => geometry.dispose());
  materials.forEach(disposeMaterial);
}

function disposeModelCache(runtime: RuntimeScene): void {
  runtime.modelCache.forEach((entry) => {
    entry.waiters = [];
    if (entry.scene) disposeObject(entry.scene);
  });
  runtime.modelCache.clear();
}

function disposeLights(runtime: RuntimeScene): void {
  runtime.lights.forEach((light) => {
    runtime.scene.remove(light);
    const shadow = (light as THREE.DirectionalLight).shadow;
    shadow?.map?.dispose();
    light.dispose();
  });
  runtime.lights = [];
}


function disposeObject(object: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (mesh.geometry) geometries.add(mesh.geometry);
    const material = mesh.material;
    if (Array.isArray(material)) material.forEach((item) => materials.add(item));
    else if (material) materials.add(material);
  });
  geometries.forEach((geometry) => geometry.dispose());
  materials.forEach(disposeMaterial);
}

function disposeMaterial(material: THREE.Material): void {
  const withMap = material as THREE.Material & { map?: THREE.Texture };
  // Persistent textures (e.g. the shared contact-shadow gradient) are reused
  // across scene rebuilds and must not be disposed with their material.
  if (withMap.map && !withMap.map.userData?.persistent) withMap.map.dispose();
  material.dispose();
}
