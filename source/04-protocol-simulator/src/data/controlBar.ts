import type { OperationFamily } from "../types";

export type ControlBarSectionId =
  | "methods"
  | "scripts"
  | "worktables"
  | "commands"
  | "liquidClasses"
  | "carriers"
  | "labware"
  | "tools"
  | "modules";

export type ControlBarCommandsGroupId =
  | "directCommands"
  | "programming"
  | "touchTools"
  | "fca1"
  | "fca2"
  | "rga1"
  | "worktable"
  | "worklist"
  | "sampleTracking"
  | "communication";

export type ControlBarCommandTemplate = {
  id: string;
  family: OperationFamily;
  name: string;
  operation: string;
  description: string;
  defaults?: Partial<{
    targetLabware: string;
    wells: string[];
    volumeUl: number | null;
    liquidClass: string;
    message: string;
    specs: Record<string, string>;
  }>;
};

export type ControlBarCommandsGroup = {
  id: ControlBarCommandsGroupId;
  label: string;
  description: string;
  commands: ControlBarCommandTemplate[];
};

export type ControlBarSection = {
  id: ControlBarSectionId;
  label: string;
  description: string;
  kind: "commands" | "objects" | "browse";
  objectFilter?: "carriers" | "labware" | "tools";
};

export const CONTROL_BAR_SECTIONS: ControlBarSection[] = [
  { id: "methods", label: "Methods", description: "Runnable method containers that group one or more scripts.", kind: "browse" },
  { id: "scripts", label: "Scripts", description: "Script collections edited in Script Editor.", kind: "browse" },
  { id: "worktables", label: "Worktables", description: "Base and script worktable layouts for the deck.", kind: "browse" },
  { id: "commands", label: "Commands", description: "Device, programming, and TouchTools script steps.", kind: "commands" },
  { id: "liquidClasses", label: "Liquid Classes", description: "Pipetting liquid class definitions.", kind: "browse" },
  { id: "carriers", label: "Carriers", description: "Deck carriers, nests, and support segments.", kind: "objects", objectFilter: "carriers" },
  { id: "labware", label: "Labware", description: "Plates, tubes, reservoirs, and other consumables.", kind: "objects", objectFilter: "labware" },
  { id: "tools", label: "Tools", description: "Fixed tools, tip boxes, and arm-mounted items.", kind: "objects", objectFilter: "tools" },
  { id: "modules", label: "Modules", description: "Reusable synchronous script modules.", kind: "browse" }
];

export const CONTROL_BAR_COMMAND_GROUPS: ControlBarCommandsGroup[] = [
  {
    id: "directCommands",
    label: "Direct Commands",
    description: "Direct arm and device actions outside normal pipetting groups.",
    commands: [
      { id: "direct-move-head", family: "motion", name: "Move Head", operation: "move_head", description: "Move the active head to a labware or site position." },
      { id: "direct-home", family: "motion", name: "Home Axis", operation: "home_axis", description: "Home an axis or device before continuing.", defaults: { specs: { axis: "" } } },
      { id: "direct-wait", family: "motion", name: "Wait", operation: "wait", description: "Wait for a duration or device state.", defaults: { specs: { seconds: "1" } } }
    ]
  },
  {
    id: "programming",
    label: "Programming",
    description: "Flow control, variables, comments, and plain operator prompts.",
    commands: [
      { id: "prog-comment", family: "comment", name: "Comment", operation: "comment", description: "Add a non-executing script comment.", defaults: { message: "Add note here." } },
      { id: "prog-section", family: "comment", name: "Section Header", operation: "section_comment", description: "Add a visible section break in the script.", defaults: { message: "New section" } },
      { id: "prog-user-prompt", family: "prompt", name: "User Prompt", operation: "user_prompt", description: "Show a text message and wait for acknowledgement.", defaults: { message: "Confirm before continuing." } },
      { id: "prog-pause", family: "prompt", name: "Pause", operation: "pause", description: "Pause execution until the operator continues.", defaults: { message: "Paused." } },
      { id: "prog-manual-step", family: "prompt", name: "Manual Step", operation: "manual_step", description: "Document a required manual operator action.", defaults: { message: "Perform manual step, then continue." } },
      { id: "prog-set-variable", family: "setup", name: "Set Variable", operation: "set_variable", description: "Create or update a script variable.", defaults: { specs: { variable: "", value: "" } } },
      { id: "prog-if", family: "flow", name: "If Condition", operation: "if_condition", description: "Branch based on a condition.", defaults: { specs: { condition: "" } } },
      { id: "prog-loop", family: "flow", name: "Loop", operation: "loop", description: "Repeat commands for a configured count.", defaults: { specs: { count: "1" } } },
      { id: "prog-subroutine", family: "flow", name: "Call Subroutine", operation: "call_subroutine", description: "Run a named subroutine.", defaults: { specs: { subroutine: "" } } },
      { id: "prog-label", family: "flow", name: "Label", operation: "label", description: "Mark a location in the script.", defaults: { specs: { label: "" } } },
      { id: "prog-init", family: "setup", name: "Initialize Script", operation: "initialize_script", description: "Start-of-script setup and instrument preparation." }
    ]
  },
  {
    id: "touchTools",
    label: "Touch Tools",
    description: "Rich user prompts shown on the TouchTools operator screen.",
    commands: [
      {
        id: "tt-standard",
        family: "prompt",
        name: "Standard",
        operation: "user_prompt",
        description: "Rich prompt with optional image, sound, and progress on TouchTools.",
        defaults: { message: "Confirm before continuing.", specs: { imagePath: "", screenTitle: "Operator check" } }
      },
      {
        id: "tt-worktable",
        family: "labware",
        name: "Worktable",
        operation: "manual_step",
        description: "Deck-bound TouchTools prompt with labware highlight and custom detail image.",
        defaults: { message: "Confirm labware placement.", specs: { customDetailImage: "" } }
      },
      {
        id: "tt-variable",
        family: "prompt",
        name: "Variable",
        operation: "user_prompt",
        description: "TouchTools runtime variable entry form.",
        defaults: { message: "Enter required value.", specs: { variableName: "" } }
      },
      { id: "tt-wizard-group", family: "flow", name: "Wizard Group", operation: "loop", description: "Group TouchTools loading or verification steps.", defaults: { specs: { count: "1" } } },
      { id: "tt-get-file", family: "setup", name: "Get File", operation: "manual_step", description: "Prompt the operator to select a file at runtime.", defaults: { message: "Select the required file." } },
      { id: "tt-select-wells", family: "liquid", name: "Select Wells", operation: "manual_step", description: "TouchTools well-selection screen for sample count.", defaults: { message: "Select wells on TouchTools." } }
    ]
  },
  {
    id: "fca1",
    label: "FCA 1",
    description: "Flexible Channel Arm 1 pipetting, tips, and wash commands.",
    commands: [
      { id: "fca1-get-tip", family: "tips", name: "Get Tip", operation: "get_tip", description: "Pick up tips from the selected tip box.", defaults: { specs: { channels: "all" } } },
      { id: "fca1-drop-tip", family: "tips", name: "Drop Tip", operation: "drop_tip", description: "Drop active tips to waste or a configured target.", defaults: { specs: { destination: "waste" } } },
      { id: "fca1-wash-tip", family: "wash", name: "Wash Tips", operation: "wash_tips", description: "Wash active washable tips at a wash station." },
      { id: "fca1-get-adapter", family: "tips", name: "Get Head Adapter", operation: "get_head_adapter", description: "Mount a head adapter before special labware steps." },
      { id: "fca1-drop-adapter", family: "tips", name: "Drop Head Adapter", operation: "drop_head_adapter", description: "Return or drop a mounted head adapter." },
      { id: "fca1-aspirate", family: "liquid", name: "Aspirate", operation: "aspirate", description: "Aspirate liquid from selected wells.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "fca1-dispense", family: "liquid", name: "Dispense", operation: "dispense", description: "Dispense liquid into selected wells.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "fca1-mix", family: "liquid", name: "Mix", operation: "mix", description: "Mix selected wells with repeated aspirate/dispense cycles.", defaults: { wells: ["A1"], volumeUl: 20, liquidClass: "Water free dispense", specs: { cycles: "3" } } },
      { id: "fca1-detect", family: "liquid", name: "Detect Liquid", operation: "detect_liquid", description: "Run liquid level detection for selected wells.", defaults: { wells: ["A1"], liquidClass: "Water free dispense" } },
      { id: "fca1-transfer", family: "liquid", name: "Transfer", operation: "transfer_liquid", description: "Move liquid from a source to a destination.", defaults: { volumeUl: 10, liquidClass: "Water free dispense", specs: { source: "", destination: "" } } },
      { id: "fca1-wash-station", family: "wash", name: "Wash Station", operation: "wash_station", description: "Run a wash station clean step." },
      { id: "fca1-prime", family: "wash", name: "Prime", operation: "prime", description: "Prime lines or wash system before liquid handling." },
      { id: "fca1-set-liquid-class", family: "setup", name: "Set Liquid Class", operation: "set_liquid_class", description: "Define the liquid class used by later liquid moves.", defaults: { liquidClass: "Water free dispense" } }
    ]
  },
  {
    id: "fca2",
    label: "FCA 2",
    description: "Flexible Channel Arm 2 pipetting commands when dual FCA is configured.",
    commands: [
      { id: "fca2-get-tip", family: "tips", name: "Get Tip", operation: "get_tip", description: "Pick up tips on FCA 2.", defaults: { specs: { channels: "all", device: "FCA 2" } } },
      { id: "fca2-drop-tip", family: "tips", name: "Drop Tip", operation: "drop_tip", description: "Drop tips on FCA 2.", defaults: { specs: { destination: "waste", device: "FCA 2" } } },
      { id: "fca2-aspirate", family: "liquid", name: "Aspirate", operation: "aspirate", description: "Aspirate liquid on FCA 2.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense", specs: { device: "FCA 2" } } },
      { id: "fca2-dispense", family: "liquid", name: "Dispense", operation: "dispense", description: "Dispense liquid on FCA 2.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense", specs: { device: "FCA 2" } } }
    ]
  },
  {
    id: "rga1",
    label: "RGA 1",
    description: "Robotic gripper arm transfer and plate handling.",
    commands: [
      { id: "rga1-move-labware", family: "labware", name: "Move Labware (Transfer Labware)", operation: "move_labware", description: "Move selected labware to a site or parent object.", defaults: { specs: { location: "", site: "" } } },
      { id: "rga1-cover", family: "labware", name: "Cover Labware", operation: "cover_labware", description: "Place a cover or lid onto labware." },
      { id: "rga1-uncover", family: "labware", name: "Uncover Labware", operation: "uncover_labware", description: "Remove a cover or lid from labware." }
    ]
  },
  {
    id: "worktable",
    label: "Worktable",
    description: "Worktable setup commands such as add, remove, and set location.",
    commands: [
      { id: "wt-add-labware", family: "labware", name: "Add Labware", operation: "register_labware", description: "Declare or add labware for the script worktable.", defaults: { specs: { labwareType: "", location: "" } } },
      { id: "wt-remove-labware", family: "labware", name: "Remove Labware", operation: "move_labware", description: "Remove labware from the active worktable.", defaults: { specs: { location: "offdeck" } } },
      { id: "wt-set-location", family: "labware", name: "Set Location", operation: "move_labware", description: "Move labware to a configured deck location.", defaults: { specs: { location: "", site: "" } } }
    ]
  },
  {
    id: "worklist",
    label: "Worklist",
    description: "Worklist-driven pipetting and transfer commands.",
    commands: [
      { id: "wl-read", family: "liquid", name: "Read Worklist", operation: "read_worklist", description: "Load the next worklist transfer row." },
      { id: "wl-aspirate", family: "liquid", name: "Worklist Aspirate", operation: "aspirate", description: "Aspirate using the active worklist row.", defaults: { volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "wl-dispense", family: "liquid", name: "Worklist Dispense", operation: "dispense", description: "Dispense using the active worklist row.", defaults: { volumeUl: 10, liquidClass: "Water free dispense" } }
    ]
  },
  {
    id: "sampleTracking",
    label: "Sample Tracking",
    description: "Barcode registration and sample tracking commands.",
    commands: [
      { id: "st-register", family: "labware", name: "Register Labware", operation: "register_labware", description: "Register labware for sample tracking.", defaults: { specs: { barcode: "" } } },
      { id: "st-scan", family: "prompt", name: "Manual Scan Labware", operation: "manual_step", description: "Operator barcode scan step on TouchTools.", defaults: { message: "Scan labware barcode." } }
    ]
  },
  {
    id: "communication",
    label: "Communication",
    description: "External applications, scripts, and reporting hooks.",
    commands: [
      { id: "comm-execute-app", family: "setup", name: "Execute Application", operation: "manual_step", description: "Launch an external application during the run.", defaults: { specs: { application: "" } } },
      { id: "comm-vbscript", family: "setup", name: "Execute VBScript", operation: "manual_step", description: "Run a VBScript hook.", defaults: { specs: { script: "" } } },
      { id: "comm-report", family: "comment", name: "Generate Report", operation: "comment", description: "Generate a TouchTools or PDF report from a template.", defaults: { message: "Generate report." } }
    ]
  }
];

export const CONTROL_BAR_LIQUID_CLASSES = [
  "Water free dispense",
  "Ethanol free single",
  "Serum free multi",
  "Mastermix free multi",
  "Mix LC"
];

export function allControlBarCommandTemplates(): ControlBarCommandTemplate[] {
  return CONTROL_BAR_COMMAND_GROUPS.flatMap((group) => group.commands);
}

export function controlBarCommandTemplateById(templateId: string): ControlBarCommandTemplate | undefined {
  return allControlBarCommandTemplates().find((template) => template.id === templateId);
}

export function controlBarSectionById(sectionId: ControlBarSectionId): ControlBarSection {
  return CONTROL_BAR_SECTIONS.find((section) => section.id === sectionId) || CONTROL_BAR_SECTIONS[0];
}

export function controlBarCommandsGroupById(groupId: ControlBarCommandsGroupId): ControlBarCommandsGroup {
  return CONTROL_BAR_COMMAND_GROUPS.find((group) => group.id === groupId) || CONTROL_BAR_COMMAND_GROUPS[0];
}

export function objectLibraryMatchesSectionFilter(tags: string[], componentRole: string, filter: ControlBarSection["objectFilter"]): boolean {
  if (!filter) return true;
  const normalizedTags = tags.map((tag) => tag.toLowerCase());
  const role = componentRole.toLowerCase();
  if (filter === "carriers") return normalizedTags.includes("carrier") || normalizedTags.includes("nest") || role === "nest";
  if (filter === "tools") return normalizedTags.includes("tips") || normalizedTags.includes("tool") || role === "tip-box";
  return !normalizedTags.includes("carrier") && !normalizedTags.includes("nest") && role !== "nest" && role !== "tip-box";
}
