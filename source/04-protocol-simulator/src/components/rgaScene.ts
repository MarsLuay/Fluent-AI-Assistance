import * as THREE from "three";
import type {
  CommandModel,
  GripperFingerSet,
  GripperOrientation,
  GripperState,
  LabwareModel,
  VerificationOverlay
} from "../types";

type LabwarePlacementLike = {
  size: {
    width: number;
    depth: number;
    height: number;
  };
};

type RgaSceneStyle = {
  armColor: string;
  fingerColor: string;
  jawOpenGap: number;
  jawClosedGap: number;
};

const DEFAULT_STYLE: RgaSceneStyle = {
  armColor: "#5f6d73",
  fingerColor: "#c7d0d6",
  jawOpenGap: 1.05,
  jawClosedGap: 0.18
};

export function addRgaGripperScene(
  root: THREE.Group,
  options: {
    command?: CommandModel;
    gripper: GripperState;
    overlay?: VerificationOverlay;
    armPosition: THREE.Vector3;
    labware: LabwareModel[];
    labwarePlacements: Map<string, LabwarePlacementLike>;
    staticMode: boolean;
  }
): THREE.Group {
  const group = new THREE.Group();
  group.name = "rga-gripper-scene";
  group.position.copy(options.armPosition);
  group.position.y = Math.max(group.position.y, 4.8);
  root.add(group);

  addRgaRail(root, options.armPosition);
  addGripperBody(group, options.gripper, options.overlay);

  if (options.gripper.jaw === "closed" && options.gripper.grippedStack.length) {
    addGrippedStack(group, options.gripper, options.labware, options.labwarePlacements);
  }

  if (options.overlay) {
    addVerificationOverlay(group, options.gripper, options.overlay, options.staticMode);
  }

  if (options.overlay?.kind === "rga_fingers") {
    addBadStateFingerDemo(group, options.gripper.fingerSet);
  }

  if (options.command?.verificationOverlay?.kind === "rga_fingers" || options.overlay?.kind === "rga_fingers") {
    addFingerOrientationGuide(group, options.gripper.orientation, options.staticMode);
  }

  return group;
}

function addRgaRail(root: THREE.Group, position: THREE.Vector3): void {
  const material = new THREE.MeshStandardMaterial({ color: "#667273", roughness: 0.46, metalness: 0.14 });
  const rail = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.12, 0.12), material);
  rail.position.set(position.x, 5.1, position.z - 0.55);
  rail.name = "rga-rail";
  root.add(rail);

  const mast = new THREE.Mesh(new THREE.BoxGeometry(0.14, 3.6, 0.14), material);
  mast.position.set(position.x, 3.3, position.z - 0.55);
  mast.name = "rga-mast";
  root.add(mast);
}

function addGripperBody(group: THREE.Group, gripper: GripperState, overlay?: VerificationOverlay): void {
  const style = fingerStyleFor(gripper.fingerSet, overlay);
  const body = new THREE.Mesh(
    new THREE.BoxGeometry(0.72, 0.34, 0.42),
    new THREE.MeshStandardMaterial({ color: style.armColor, roughness: 0.42, metalness: 0.2 })
  );
  body.position.set(0, 0, 0);
  group.add(body);

  const fingerGroup = new THREE.Group();
  fingerGroup.name = "rga-fingers";
  group.add(fingerGroup);

  const gap = gripper.jaw === "closed" ? style.jawClosedGap : style.jawOpenGap;
  const fingerLength = fingerLengthFor(gripper.fingerSet);
  const fingerWidth = fingerWidthFor(gripper.fingerSet);
  const fingerHeight = fingerHeightFor(gripper.fingerSet);
  const fingerMaterial = new THREE.MeshStandardMaterial({ color: style.fingerColor, roughness: 0.38, metalness: 0.12 });

  const leftFinger = buildFingerMesh(fingerWidth, fingerHeight, fingerLength, fingerMaterial, gripper.orientation, -1);
  leftFinger.position.set(-gap / 2, -0.08, fingerLength / 2 + 0.08);
  fingerGroup.add(leftFinger);

  const rightFinger = buildFingerMesh(fingerWidth, fingerHeight, fingerLength, fingerMaterial, gripper.orientation, 1);
  rightFinger.position.set(gap / 2, -0.08, fingerLength / 2 + 0.08);
  fingerGroup.add(rightFinger);

  if (gripper.fingerSet === "tube" || gripper.fingerSet === "cap") {
    const pad = new THREE.Mesh(
      new THREE.CylinderGeometry(0.12, 0.12, 0.08, 20),
      new THREE.MeshStandardMaterial({ color: "#8d98a0", roughness: 0.5, metalness: 0.08 })
    );
    pad.rotation.x = Math.PI / 2;
    pad.position.set(0, -0.04, fingerLength / 2 + 0.18);
    fingerGroup.add(pad);
  }

  const label = makeTextSprite(gripperLabel(gripper), "#1f2a30", "rgba(255,255,255,0.86)");
  label.position.set(0, 0.72, 0);
  label.scale.set(2.2, 0.5, 1);
  group.add(label);
}

function buildFingerMesh(
  width: number,
  height: number,
  length: number,
  material: THREE.Material,
  orientation: GripperOrientation,
  side: -1 | 1
): THREE.Group {
  const finger = new THREE.Group();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, length), material);
  mesh.position.z = length / 2;
  finger.add(mesh);

  const edge = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(width * 1.02, height * 1.04, length * 1.02)),
    new THREE.LineBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.35 })
  );
  edge.position.copy(mesh.position);
  finger.add(edge);

  if (orientation === "diagonal") {
    finger.rotation.y = side * THREE.MathUtils.degToRad(24);
  } else if (orientation === "parallel") {
    finger.rotation.y = 0;
  }
  return finger;
}

function addGrippedStack(
  group: THREE.Group,
  gripper: GripperState,
  labwareItems: LabwareModel[],
  placements: Map<string, LabwarePlacementLike>
): void {
  const stackGroup = new THREE.Group();
  stackGroup.name = "rga-gripped-stack";
  stackGroup.position.set(0, -0.42, 1.05);
  group.add(stackGroup);

  let offsetY = 0;
  gripper.grippedStack.forEach((label, index) => {
    const labware = labwareItems.find((item) => item.label === label) || labwareItems.find((item) => label.includes(item.label));
    const placement = labware ? placements.get(labware.label) : undefined;
    const widthMm = labware ? labware.physicalWidthMm ?? 0 : 0;
    const depthMm = labware ? labware.physicalDepthMm ?? 0 : 0;
    const heightMm = labware ? labware.physicalHeightMm ?? 0 : 0;
    const width = placement?.size.width || widthMm / 10 || 1.1;
    const depth = placement?.size.depth || depthMm / 10 || 0.8;
    const height = Math.max(0.22, Math.min(placement?.size.height || heightMm / 10 || 0.35, 1.1));
    const color = index === gripper.grippedStack.length - 1 ? "#f2b134" : "#8aa0ad";
    const body = new THREE.Mesh(
      new THREE.BoxGeometry(width, height, depth),
      new THREE.MeshStandardMaterial({
        color,
        emissive: color,
        emissiveIntensity: 0.12,
        transparent: true,
        opacity: 0.72,
        roughness: 0.42
      })
    );
    body.position.y = offsetY + height / 2;
    stackGroup.add(body);
    const edge = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(width * 1.02, height * 1.03, depth * 1.02)),
      new THREE.LineBasicMaterial({ color: "#fff2b8", transparent: true, opacity: 0.8 })
    );
    edge.position.copy(body.position);
    stackGroup.add(edge);
    offsetY += height + 0.04;
  });
}

function addVerificationOverlay(
  group: THREE.Group,
  gripper: GripperState,
  overlay: VerificationOverlay,
  staticMode: boolean
): void {
  const panel = new THREE.Group();
  panel.name = "rga-verification-overlay";
  panel.position.set(0, 1.35, 0.95);
  group.add(panel);

  const title = makeTextSprite(overlay.title, "#ffffff", "rgba(20,34,40,0.88)");
  title.position.set(0, 0.42, 0);
  title.scale.set(2.8, 0.55, 1);
  panel.add(title);

  const expectLabel = makeTextSprite(`Expected: ${overlay.expect.replace(/_/g, " ")}`, "#d9f7e8", "rgba(12,58,42,0.9)");
  expectLabel.position.set(-0.95, 0, 0);
  expectLabel.scale.set(2.1, 0.42, 1);
  panel.add(expectLabel);

  overlay.badStates.forEach((badState, index) => {
    const bad = makeTextSprite(`Bad: ${badState}`, "#ffd5d5", "rgba(74,18,18,0.9)");
    bad.position.set(0.95, -0.18 - index * 0.28, 0);
    bad.scale.set(1.8, 0.38, 1);
    panel.add(bad);
  });

  if (overlay.kind === "rga_fingers" && gripper.orientation === "parallel") {
    const halo = new THREE.Mesh(
      new THREE.RingGeometry(0.72, 0.86, 36),
      new THREE.MeshBasicMaterial({ color: "#4fd18b", transparent: true, opacity: staticMode ? 0.55 : 0.35, side: THREE.DoubleSide })
    );
    halo.rotation.x = -Math.PI / 2;
    halo.position.set(0, -0.2, 0.55);
    panel.add(halo);
  }
}

function addBadStateFingerDemo(group: THREE.Group, fingerSet: GripperFingerSet): void {
  const demo = new THREE.Group();
  demo.name = "rga-bad-state-demo";
  demo.position.set(-1.55, -0.05, 0.2);
  group.add(demo);

  const material = new THREE.MeshStandardMaterial({
    color: "#ef6a63",
    emissive: "#8f2018",
    emissiveIntensity: 0.22,
    transparent: true,
    opacity: 0.82,
    roughness: 0.45
  });
  const length = fingerLengthFor(fingerSet);
  const width = fingerWidthFor(fingerSet);
  const height = fingerHeightFor(fingerSet);
  const left = buildFingerMesh(width, height, length, material, "diagonal", -1);
  left.position.set(-0.42, 0, 0);
  demo.add(left);
  const right = buildFingerMesh(width, height, length, material, "diagonal", 1);
  right.position.set(0.42, 0, 0);
  demo.add(right);

  const label = makeTextSprite("bad state", "#ffffff", "rgba(120,24,20,0.92)");
  label.position.set(0, 0.72, 0.15);
  label.scale.set(1.3, 0.32, 1);
  demo.add(label);
}

function addFingerOrientationGuide(group: THREE.Group, orientation: GripperOrientation, staticMode: boolean): void {
  const guide = new THREE.Group();
  guide.name = "rga-orientation-guide";
  guide.position.set(1.35, 0.1, 0.45);
  group.add(guide);

  const good = buildFingerMesh(0.14, 0.42, 0.72, new THREE.MeshStandardMaterial({ color: "#63d39b", transparent: true, opacity: 0.78 }), "parallel", -1);
  good.position.set(-0.22, 0, 0);
  guide.add(good);
  const goodLabel = makeTextSprite("parallel", "#0f3d2d", "rgba(214,255,236,0.92)");
  goodLabel.position.set(-0.22, 0.55, 0.2);
  goodLabel.scale.set(1.2, 0.28, 1);
  guide.add(goodLabel);

  const bad = buildFingerMesh(0.14, 0.42, 0.72, new THREE.MeshStandardMaterial({ color: "#ef6a63", transparent: true, opacity: 0.72 }), "diagonal", 1);
  bad.position.set(0.42, 0, 0);
  guide.add(bad);
  const badLabel = makeTextSprite("diagonal", "#4d1411", "rgba(255,224,220,0.92)");
  badLabel.position.set(0.42, 0.55, 0.2);
  badLabel.scale.set(1.2, 0.28, 1);
  guide.add(badLabel);

  if (orientation === "parallel" && staticMode) {
    const arrow = makeTextSprite("check this", "#ffffff", "rgba(31,42,48,0.9)");
    arrow.position.set(-0.22, 0.82, 0);
    arrow.scale.set(1.1, 0.28, 1);
    guide.add(arrow);
  }
}

function fingerStyleFor(fingerSet: GripperFingerSet, overlay?: VerificationOverlay): RgaSceneStyle {
  if (overlay?.kind === "tube_cap_gripper" || fingerSet === "tube" || fingerSet === "cap") {
    return { ...DEFAULT_STYLE, fingerColor: "#aeb8bf", jawOpenGap: 0.72, jawClosedGap: 0.12 };
  }
  return DEFAULT_STYLE;
}

function fingerLengthFor(fingerSet: GripperFingerSet): number {
  if (fingerSet === "tube" || fingerSet === "cap") return 0.62;
  return 0.92;
}

function fingerWidthFor(fingerSet: GripperFingerSet): number {
  if (fingerSet === "tube" || fingerSet === "cap") return 0.1;
  return 0.16;
}

function fingerHeightFor(fingerSet: GripperFingerSet): number {
  if (fingerSet === "tube" || fingerSet === "cap") return 0.34;
  return 0.48;
}

function gripperLabel(gripper: GripperState): string {
  const set = gripper.fingerSet === "unknown" ? "RGA" : gripper.fingerSet.toUpperCase();
  const jaw = gripper.jaw === "unknown" ? "" : ` · ${gripper.jaw}`;
  const orientation = gripper.orientation === "unknown" ? "" : ` · ${gripper.orientation}`;
  return `${set}${jaw}${orientation}`;
}

function makeTextSprite(text: string, color: string, background: string): THREE.Sprite {
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  const fontSize = 28;
  const padding = 12;
  if (!context) {
    const material = new THREE.SpriteMaterial();
    return new THREE.Sprite(material);
  }
  context.font = `600 ${fontSize}px Segoe UI, Arial, sans-serif`;
  const metrics = context.measureText(text);
  canvas.width = Math.ceil(metrics.width + padding * 2);
  canvas.height = fontSize + padding * 2;
  context.font = `600 ${fontSize}px Segoe UI, Arial, sans-serif`;
  context.fillStyle = background;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = color;
  context.textBaseline = "middle";
  context.fillText(text, padding, canvas.height / 2);
  const texture = new THREE.CanvasTexture(canvas);
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(canvas.width / 90, canvas.height / 90, 1);
  return sprite;
}
