import type { ProtocolIrOperation } from "./protocolIrContract";
import { isProtocolIrOperation } from "./protocolIrOperations";
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

export type ControlBarCommandDefaults = {
  targetLabware?: string;
  wells?: string[];
  volumeUl?: number | null;
  liquidClass?: string;
  message?: string;
  specs?: Record<string, string>;
};

export type ControlBarCommandTemplate = {
  id: string;
  family: OperationFamily;
  name: string;
  operation: ProtocolIrOperation;
  description: string;
  defaults?: ControlBarCommandDefaults;
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

/**
 * The simulator's only authorable command catalog. Operations are typed from
 * the generated Protocol IR contract and checked again at runtime by tests and
 * callers that load this catalog.
 */
export const CONTROL_BAR_COMMAND_GROUPS: ControlBarCommandsGroup[] = [
  {
    id: "directCommands",
    label: "Direct Commands",
    description: "Source-backed FluentControl motion and device operations.",
    commands: [
      {
        id: "direct-move-axis",
        family: "motion",
        name: "Move Axis",
        operation: "move_axis_command",
        description: "Validate a source-backed axis move without claiming physical execution.",
        defaults: { specs: { available_id: "", id_label: "", position_expression: "" } }
      },
      {
        id: "direct-start-move",
        family: "motion",
        name: "Start Move",
        operation: "start_move_command",
        description: "Preserve a source-backed asynchronous move start for offline validation.",
        defaults: { specs: { available_id: "", id_label: "" } }
      },
      {
        id: "direct-wait-async",
        family: "motion",
        name: "Wait For Async Response",
        operation: "wait_for_async_response",
        description: "Wait for a source-backed asynchronous response during offline validation."
      }
    ]
  },
  {
    id: "programming",
    label: "Programming",
    description: "Flow control, variables, comments, and runtime prompts.",
    commands: [
      { id: "prog-comment", family: "comment", name: "Comment", operation: "comment", description: "Add a non-executing script comment.", defaults: { message: "Add note here." } },
      { id: "prog-set-variable", family: "setup", name: "Set Variable", operation: "set_variable", description: "Create or update a script variable.", defaults: { specs: { variable: "", value: "" } } },
      { id: "prog-if", family: "flow", name: "Conditional Branch", operation: "conditional_branch", description: "Branch based on a source-backed condition.", defaults: { specs: { condition: "" } } },
      { id: "prog-default-branch", family: "flow", name: "Default Branch", operation: "default_branch", description: "Declare the default branch of a conditional sequence." },
      { id: "prog-loop", family: "flow", name: "Loop Over Wells", operation: "loop_over_wells", description: "Repeat a source-backed well iteration.", defaults: { specs: { count: "1" } } },
      { id: "prog-query-variable", family: "flow", name: "Query Variable", operation: "query_variable", description: "Read a source-backed runtime variable.", defaults: { specs: { variable: "" } } },
      { id: "prog-runtime-prompt", family: "prompt", name: "Runtime Variable Prompt", operation: "runtime_variable_prompt", description: "Request a runtime variable value.", defaults: { message: "Enter required value.", specs: { variable: "" } } },
      { id: "prog-set-runtime", family: "setup", name: "Set Remaining Runtime", operation: "set_remaining_runtime", description: "Set the source-backed remaining runtime value." },
      { id: "prog-subroutine", family: "flow", name: "Call Subroutine", operation: "call_subroutine", description: "Run a named source-backed subroutine.", defaults: { specs: { subroutine: "" } } },
      { id: "prog-user-prompt", family: "prompt", name: "User Prompt", operation: "prompt_user", description: "Show a text message and wait for acknowledgement.", defaults: { message: "Confirm before continuing." } }
    ]
  },
  {
    id: "touchTools",
    label: "Touch Tools",
    description: "Rich operator prompts represented by canonical Protocol IR operations.",
    commands: [
      { id: "tt-standard", family: "prompt", name: "Standard", operation: "prompt_user", description: "Show a canonical operator prompt.", defaults: { message: "Confirm before continuing.", specs: { screenTitle: "Operator check" } } },
      { id: "tt-variable", family: "prompt", name: "Variable", operation: "runtime_variable_prompt", description: "Request a runtime variable value on the operator screen.", defaults: { message: "Enter required value.", specs: { variable: "" } } },
      { id: "tt-default-branch", family: "flow", name: "Default Branch", operation: "default_branch", description: "Represent the default branch of a TouchTools sequence." }
    ]
  },
  {
    id: "fca1",
    label: "FCA 1",
    description: "Flexible Channel Arm 1 pipetting, tips, and wash operations.",
    commands: [
      { id: "fca1-get-tip", family: "tips", name: "Get Tips", operation: "pick_up_tips", description: "Pick up tips from the selected tip box.", defaults: { specs: { channels: "all" } } },
      { id: "fca1-drop-tip", family: "tips", name: "Drop Tips", operation: "drop_tips", description: "Drop active tips to waste or a configured target.", defaults: { specs: { destination: "waste" } } },
      { id: "fca1-wash-tip", family: "wash", name: "Wash Tips", operation: "wash", description: "Wash active washable tips at a wash station." },
      { id: "fca1-get-adapter", family: "tips", name: "Get Head Adapter", operation: "get_head_adapter", description: "Mount a head adapter before special labware steps." },
      { id: "fca1-drop-adapter", family: "tips", name: "Drop Head Adapter", operation: "drop_head_adapter", description: "Return or drop a mounted head adapter." },
      { id: "fca1-aspirate", family: "liquid", name: "Aspirate", operation: "aspirate", description: "Aspirate liquid from selected wells.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "fca1-dispense", family: "liquid", name: "Dispense", operation: "dispense", description: "Dispense liquid into selected wells.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "fca1-mix", family: "liquid", name: "Mix", operation: "mix", description: "Mix selected wells with repeated aspirate/dispense cycles.", defaults: { wells: ["A1"], volumeUl: 20, liquidClass: "Water free dispense", specs: { cycles: "3" } } },
      { id: "fca1-mca384-get-tip", family: "tips", name: "MCA384 Get Tips", operation: "mca384_get_tips", description: "Pick up an MCA384 tip set from the selected tip box." },
      { id: "fca1-mca384-drop-tip", family: "tips", name: "MCA384 Drop Tips", operation: "mca384_drop_tips", description: "Drop an MCA384 tip set to the configured destination." },
      { id: "fca1-mca384-mix", family: "liquid", name: "MCA384 Mix", operation: "mca384_mix", description: "Mix with the source-backed MCA384 operation.", defaults: { volumeUl: 20, liquidClass: "Water free dispense" } }
    ]
  },
  {
    id: "fca2",
    label: "FCA 2",
    description: "Flexible Channel Arm 2 canonical pipetting operations.",
    commands: [
      { id: "fca2-get-tip", family: "tips", name: "LiHa Get Tips", operation: "liha_get_tips", description: "Pick up LiHa tips from the selected tip box." },
      { id: "fca2-drop-tip", family: "tips", name: "LiHa Drop Tips", operation: "liha_drop_tips", description: "Drop LiHa tips to the configured destination." },
      { id: "fca2-aspirate", family: "liquid", name: "LiHa Aspirate", operation: "liha_aspirate", description: "Aspirate using the canonical LiHa operation.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "fca2-dispense", family: "liquid", name: "LiHa Dispense", operation: "liha_dispense", description: "Dispense using the canonical LiHa operation.", defaults: { wells: ["A1"], volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "fca2-mix", family: "liquid", name: "LiHa Mix", operation: "liha_mix", description: "Mix using the canonical LiHa operation.", defaults: { wells: ["A1"], volumeUl: 20, liquidClass: "Water free dispense" } }
    ]
  },
  {
    id: "rga1",
    label: "RGA 1",
    description: "Robotic gripper arm labware transfer operations.",
    commands: [
      { id: "rga1-move-labware", family: "labware", name: "Move Plate", operation: "move_plate", description: "Move labware using the canonical plate-transfer operation.", defaults: { targetLabware: "", specs: { location: "", site: "" } } }
    ]
  },
  {
    id: "worktable",
    label: "Worktable",
    description: "Canonical worktable setup operations.",
    commands: [
      { id: "wt-add-labware", family: "labware", name: "Add Labware", operation: "add_labware", description: "Declare or add labware for the script worktable.", defaults: { targetLabware: "", specs: { labwareType: "", location: "" } } },
      { id: "wt-load-labware", family: "labware", name: "Load Labware", operation: "load_labware", description: "Load source-backed labware into the script worktable.", defaults: { targetLabware: "" } }
    ]
  },
  {
    id: "worklist",
    label: "Worklist",
    description: "Worklist-driven canonical pipetting operations.",
    commands: [
      { id: "wl-read", family: "setup", name: "Read Worklist", operation: "read_worklist", description: "Load the next worklist transfer row." },
      { id: "wl-aspirate", family: "liquid", name: "Worklist Aspirate", operation: "aspirate", description: "Aspirate using the active worklist row.", defaults: { volumeUl: 10, liquidClass: "Water free dispense" } },
      { id: "wl-dispense", family: "liquid", name: "Worklist Dispense", operation: "dispense", description: "Dispense using the active worklist row.", defaults: { volumeUl: 10, liquidClass: "Water free dispense" } }
    ]
  },
  {
    id: "sampleTracking",
    label: "Sample Tracking",
    description: "Source-backed variable operations for sample tracking workflows.",
    commands: [
      { id: "st-query-variable", family: "flow", name: "Query Variable", operation: "query_variable", description: "Query a source-backed sample variable.", defaults: { specs: { variable: "" } } },
      { id: "st-runtime-prompt", family: "prompt", name: "Runtime Variable Prompt", operation: "runtime_variable_prompt", description: "Request a sample value at runtime.", defaults: { message: "Enter sample value.", specs: { variable: "" } } }
    ]
  },
  {
    id: "communication",
    label: "Communication",
    description: "Canonical external application and driver operations.",
    commands: [
      { id: "comm-execute-app", family: "setup", name: "Execute Application", operation: "execute_application", description: "Represent a source-backed external application call.", defaults: { specs: { application: "" } } },
      { id: "comm-vbscript", family: "setup", name: "Execute VBScript", operation: "execute_vb_script", description: "Represent a source-backed VBScript call.", defaults: { specs: { script: "" } } },
      { id: "comm-driver-macro", family: "setup", name: "Application Driver Macro", operation: "application_driver_macro", description: "Represent a source-backed application driver macro.", defaults: { specs: { macro: "" } } }
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

export type ControlBarCatalogDiagnostic = {
  templateId: string;
  operation: string;
  message: string;
};

export function allControlBarCommandTemplates(): ControlBarCommandTemplate[] {
  return CONTROL_BAR_COMMAND_GROUPS.flatMap((group) => group.commands);
}

export function controlBarCatalogDiagnostics(): ControlBarCatalogDiagnostic[] {
  return allControlBarCommandTemplates()
    .filter((template) => !isProtocolIrOperation(template.operation))
    .map((template) => ({
      templateId: template.id,
      operation: template.operation,
      message: `Template ${template.id} references unregistered Protocol IR operation ${template.operation}.`
    }));
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
