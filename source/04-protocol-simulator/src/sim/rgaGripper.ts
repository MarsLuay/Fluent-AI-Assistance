import type {
  CommandModel,
  GripperFingerSet,
  GripperState,
  LabwareTransferModel,
  VerificationOverlay,
  VerificationStepModel
} from "../types";

export function emptyGripperState(): GripperState {
  return {
    mounted: false,
    fingerSet: "unknown",
    orientation: "unknown",
    jaw: "unknown",
    grippedLabware: [],
    grippedStack: []
  };
}

export function cloneGripperState(state: GripperState): GripperState {
  return {
    ...state,
    grippedLabware: [...state.grippedLabware],
    grippedStack: [...state.grippedStack]
  };
}

export function applyGripperCommand(command: CommandModel, gripper: GripperState): void {
  const op = command.operation.toLowerCase();
  const message = normalizeText(command.message);
  const group = normalizeText(command.group);

  if (op === "call_subroutine") {
    applySubroutineGripperCue(command, gripper);
    return;
  }

  if (op === "move_plate" || op === "transfer_labware") {
    gripper.mounted = true;
    if (gripper.fingerSet === "unknown") gripper.fingerSet = inferFingerSetFromContext(group, message);
    gripper.jaw = "closed";
    gripper.orientation = gripper.orientation === "unknown" ? "parallel" : gripper.orientation;
    const labware = command.targetLabware || command.labwareTransfer?.labware || "";
    if (labware) {
      gripper.grippedLabware = [labware];
      const stack = [labware];
      const anchor = command.labwareTransfer?.anchorLabware;
      if (anchor && anchor !== labware) stack.unshift(anchor);
      gripper.grippedStack = stack;
    }
    return;
  }

  if (op === "prompt_user") {
    const overlay = command.verificationOverlay;
    if (overlay?.kind === "rga_fingers") {
      gripper.mounted = true;
      if (gripper.fingerSet === "unknown") gripper.fingerSet = "plate";
      gripper.orientation = overlay.expect === "parallel" ? "parallel" : gripper.orientation;
      if (gripper.jaw === "unknown") gripper.jaw = "open";
    }
    if (overlay?.kind === "labware_seating") {
      gripper.jaw = "open";
      gripper.grippedLabware = [];
      gripper.grippedStack = [];
    }
    return;
  }

  if (/drop.?finger|cga.?drop|release.?finger/.test(op) || /drop.?finger/.test(message)) {
    gripper.mounted = false;
    gripper.jaw = "open";
    gripper.grippedLabware = [];
    gripper.grippedStack = [];
    gripper.orientation = "unknown";
    gripper.fingerSet = "unknown";
  }
}

export function labwareTransferFromIrMovePlate(
  operation: string,
  parameters: Record<string, unknown>,
  targetLabware: string
): LabwareTransferModel | null {
  if (operation !== "move_plate") return null;
  const labware = stringValue(parameters.labware) || targetLabware;
  if (!labware) return null;

  const ontoLabware = stringValue(parameters.onto_labware);
  const destinationLocation = stringValue(parameters.destination_location);
  const destinationSite = stringValue(parameters.destination_site) || "1";

  if (ontoLabware) {
    return {
      labware,
      location: ontoLabware,
      site: destinationSite,
      fixedSite: true,
      moveToBase: false,
      anchorLabware: ontoLabware,
      destinationKind: "cover_site",
      rawLocation: ontoLabware,
      rawSite: destinationSite
    };
  }

  if (destinationLocation) {
    return {
      labware,
      location: destinationLocation,
      site: destinationSite,
      fixedSite: true,
      moveToBase: false,
      destinationKind: "fixed_site",
      rawLocation: destinationLocation,
      rawSite: destinationSite
    };
  }

  return null;
}

export function enrichIrCommandWithRgaCues(
  command: CommandModel,
  parameters: Record<string, unknown>
): CommandModel {
  const message =
    stringValue(parameters.prompt) ||
    stringValue(parameters.message) ||
    command.message ||
    stringValue(parameters.comment);
  const mediaFocus = mediaFocusFromParameters(parameters);
  const verificationOverlay = verificationOverlayFromText(message, mediaFocus, command.group);
  const motionKind = inferMotionKind(command);
  const labwareTransfer =
    command.labwareTransfer || labwareTransferFromIrMovePlate(command.operation, parameters, command.targetLabware);

  return {
    ...command,
    message,
    motionKind,
    verificationOverlay,
    labwareTransfer: labwareTransfer || command.labwareTransfer
  };
}

export function verificationOverlayFromText(
  message: string,
  mediaFocus: string,
  group = ""
): VerificationOverlay | undefined {
  const text = normalizeText(`${message} ${mediaFocus} ${group}`);
  if (!text) return undefined;

  if (/rga finger|fingers on the arm|mounted rga|parallel.*diagonal|diagonal.*parallel|finger.*parallel|finger.*diagonal/.test(text)) {
    return {
      kind: "rga_fingers",
      expect: "parallel",
      badStates: ["diagonal", "crossed", "twisted", "wobbling"],
      title: "RGA finger orientation",
      detail: "Fingers should stay parallel and level. Diagonal, crossed, twisted, or wobbling mounts are bad."
    };
  }

  if (/seated flat|seating|wiggling|tilt|loose seat|collision risk|positioning is correct/.test(text)) {
    return {
      kind: "labware_seating",
      expect: "seated_flat",
      badStates: ["tilted", "wiggling", "loose", "collision"],
      title: "Labware seating",
      detail: "Labware should sit flat and stable on the nest or adapter."
    };
  }

  if (/vial gripper|tube gripper|cap.*grip|gripper close|tubeye/.test(text)) {
    return {
      kind: "tube_cap_gripper",
      expect: "aligned",
      badStates: ["offset", "crushed", "missed"],
      title: "Tube / cap gripper",
      detail: "Tube or cap should be centered in the vial gripper jaws without crush or slip."
    };
  }

  return undefined;
}

export function verificationOverlayForStep(
  command: CommandModel,
  verificationSteps: VerificationStepModel[]
): VerificationOverlay | undefined {
  if (command.verificationOverlay) return command.verificationOverlay;
  const step = verificationSteps.find((entry) => entry.stepId && entry.stepId === command.id);
  if (!step) return undefined;
  if (step.type === "prompt") return verificationOverlayFromText(step.prompt || "", "", step.group || command.group);
  if (step.type === "manual_move") return verificationOverlayFromText("", `${step.labware || ""} ${step.toLocation || ""}`, step.group || "");
  return undefined;
}

export function verificationStepsFromGenerationPayload(payload: Record<string, unknown>): VerificationStepModel[] {
  const generation = isRecord(payload.generation) ? payload.generation : payload;
  const rawSteps = Array.isArray(generation.verification_steps) ? generation.verification_steps : [];
  return rawSteps
    .map((entry) => (isRecord(entry) ? verificationStepFromRecord(entry) : null))
    .filter((entry): entry is VerificationStepModel => Boolean(entry));
}

export function isRgaRelevantCommand(command: CommandModel | undefined, gripper: GripperState | undefined): boolean {
  if (!command) return Boolean(gripper?.mounted);
  if (command.motionKind === "rga") return true;
  if (command.verificationOverlay) return true;
  if (command.operation === "move_plate" || command.operation === "call_subroutine") return true;
  if (command.labwareTransfer && gripper?.mounted) return true;
  return Boolean(gripper?.mounted);
}

function applySubroutineGripperCue(command: CommandModel, gripper: GripperState): void {
  const subroutine = normalizeText(command.message || command.targetLabware);
  const group = normalizeText(command.group);
  if (/get.?finger|get_fingers|sub_get_fingers/.test(subroutine) || /get.?finger/.test(group)) {
    gripper.mounted = true;
    gripper.fingerSet = inferFingerSetFromSubroutine(command, group);
    gripper.jaw = "open";
    gripper.orientation = "parallel";
    return;
  }
  if (/cap|tube|vial|scan.*tube|gripper/.test(`${subroutine} ${group}`)) {
    gripper.mounted = true;
    gripper.fingerSet = /cap/.test(`${subroutine} ${group}`) ? "cap" : "tube";
    gripper.jaw = "closed";
    gripper.orientation = "parallel";
  }
}

function inferMotionKind(command: CommandModel): CommandModel["motionKind"] {
  const op = command.operation.toLowerCase();
  const id = command.commandId.toLowerCase();
  const group = command.group.toLowerCase();
  if (op === "move_plate" || op === "call_subroutine" || /rga|cga|gripper|transfer_labware/.test(`${id} ${group}`)) {
    return "rga";
  }
  if (command.verificationOverlay?.kind === "rga_fingers" || command.verificationOverlay?.kind === "tube_cap_gripper") {
    return "rga";
  }
  return command.motionKind || "unknown";
}

function inferFingerSetFromSubroutine(command: CommandModel, group: string): GripperFingerSet {
  const text = normalizeText(`${command.message} ${group}`);
  if (/tube|vial|50ml|15ml/.test(text)) return "tube";
  if (/cap/.test(text)) return "cap";
  return "plate";
}

function inferFingerSetFromContext(group: string, message: string): GripperFingerSet {
  const text = normalizeText(`${group} ${message}`);
  if (/tube|vial|falcon/.test(text)) return "tube";
  if (/cap/.test(text)) return "cap";
  return "plate";
}

function mediaFocusFromParameters(parameters: Record<string, unknown>): string {
  const placeholders = Array.isArray(parameters.media_placeholders) ? parameters.media_placeholders : [];
  return placeholders
    .map((entry) => (isRecord(entry) ? stringValue(entry.focus) : ""))
    .filter(Boolean)
    .join(" ");
}

function verificationStepFromRecord(record: Record<string, unknown>): VerificationStepModel | null {
  const type = stringValue(record.type);
  if (!type) return null;
  return {
    group: stringValue(record.group),
    type: type as VerificationStepModel["type"],
    prompt: stringValue(record.prompt),
    comment: stringValue(record.comment),
    subroutine: stringValue(record.subroutine),
    labware: stringValue(record.labware),
    toLocation: stringValue(record.to_location),
    toSite: numberOrNull(record.to_site),
    stepId: stringValue(record.step_id),
    name: stringValue(record.name)
  };
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : value === null || value === undefined ? "" : String(value);
}

function numberOrNull(value: unknown): number | null {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeText(value: string): string {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
