import { useEffect, useMemo, useState, type DragEvent } from "react";
import {
  CONTROL_BAR_COMMAND_GROUPS,
  CONTROL_BAR_LIQUID_CLASSES,
  CONTROL_BAR_SECTIONS,
  controlBarCommandsGroupById,
  controlBarSectionById,
  objectLibraryMatchesSectionFilter,
  type ControlBarCommandTemplate,
  type ControlBarCommandsGroup,
  type ControlBarCommandsGroupId,
  type ControlBarSection,
  type ControlBarSectionId
} from "../data/controlBar";
import type { ObjectLibraryItem } from "../types";

type ControlBarProps = {
  selectedSection: ControlBarSectionId;
  selectedCommandsGroup: ControlBarCommandsGroupId;
  onSelectSection: (sectionId: ControlBarSectionId) => void;
  onSelectCommandsGroup: (groupId: ControlBarCommandsGroupId) => void;
  objectLibrary: ObjectLibraryItem[];
  objectSearch: string;
  objectsEnabled: boolean;
  activeObjectId: string;
  onAddCommand: (template: ControlBarCommandTemplate) => void;
  onCommandDragStart: (event: DragEvent<HTMLElement>, template: ControlBarCommandTemplate) => void;
  onObjectDragStart: (event: DragEvent<HTMLElement>, item: ObjectLibraryItem) => void;
  onObjectDragEnd: () => void;
};

type MethodsBranchId = "scripts" | "worktables" | "commands";
type WorktableBranchId = "carriers" | "labware" | "tools";
type ModulesBranchId = "programming" | "communication";

type ChildTab = {
  id: string;
  label: string;
  count?: number;
};

const METHODS_BRANCHES: Array<{ id: MethodsBranchId; label: string }> = [
  { id: "scripts", label: "Scripts" },
  { id: "worktables", label: "Worktables" },
  { id: "commands", label: "Commands" }
];

const WORKTABLE_BRANCHES: Array<{ id: WorktableBranchId; label: string }> = [
  { id: "carriers", label: "Carriers" },
  { id: "labware", label: "Labware" },
  { id: "tools", label: "Tools" }
];

const MODULE_BRANCHES: Array<{ id: ModulesBranchId; label: string }> = [
  { id: "programming", label: "Programming" },
  { id: "communication", label: "Communication" }
];

function normalizeSearchText(value: string): string {
  return value.trim().toLowerCase();
}

function objectLibrarySummary(item: ObjectLibraryItem): string {
  const rows = item.rows ?? 0;
  const cols = item.cols ?? 0;
  if (rows > 0 && cols > 0) return `${rows}×${cols} · ${item.profile}`;
  return item.profile;
}

function commandGroupTabs(groups: ControlBarCommandsGroup[]): ChildTab[] {
  return groups.map((group) => ({
    id: group.id,
    label: group.label,
    count: group.commands.length
  }));
}

function TabRow({
  tabs,
  activeId,
  onSelect,
  ariaLabel
}: {
  tabs: ChildTab[];
  activeId: string;
  onSelect: (id: string) => void;
  ariaLabel: string;
}) {
  if (!tabs.length) return null;
  return (
    <div className="control-bar-child-tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={`control-bar-child-tab ${tab.id === activeId ? "is-active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab.id === activeId}
          onClick={() => onSelect(tab.id)}
        >
          <span>{tab.label}</span>
          {typeof tab.count === "number" ? <small>{tab.count}</small> : null}
        </button>
      ))}
    </div>
  );
}

export function ControlBar({
  selectedSection,
  selectedCommandsGroup,
  onSelectSection,
  onSelectCommandsGroup,
  objectLibrary,
  objectSearch,
  objectsEnabled,
  activeObjectId,
  onAddCommand,
  onCommandDragStart,
  onObjectDragStart,
  onObjectDragEnd
}: ControlBarProps) {
  const [methodsBranch, setMethodsBranch] = useState<MethodsBranchId>("scripts");
  const [worktableBranch, setWorktableBranch] = useState<WorktableBranchId>("carriers");
  const [modulesBranch, setModulesBranch] = useState<ModulesBranchId>("programming");

  const normalizedSearch = normalizeSearchText(objectSearch);
  const activeSection = controlBarSectionById(selectedSection);

  useEffect(() => {
    if (selectedSection === "worktables") setWorktableBranch("carriers");
    if (selectedSection === "carriers") setWorktableBranch("carriers");
    if (selectedSection === "labware") setWorktableBranch("labware");
    if (selectedSection === "tools") setWorktableBranch("tools");
    if (selectedSection === "modules") setModulesBranch("programming");
  }, [selectedSection]);

  const activeCommandsGroup = useMemo(() => {
    if (selectedSection === "modules") return controlBarCommandsGroupById(modulesBranch);
    return controlBarCommandsGroupById(selectedCommandsGroup);
  }, [modulesBranch, selectedCommandsGroup, selectedSection]);

  const commandGroupsForView = useMemo(() => {
    if (selectedSection === "modules") {
      return CONTROL_BAR_COMMAND_GROUPS.filter((group) => group.id === modulesBranch);
    }
    return CONTROL_BAR_COMMAND_GROUPS;
  }, [modulesBranch, selectedSection]);

  const showCommandGroups =
    selectedSection === "commands" ||
    selectedSection === "scripts" ||
    (selectedSection === "methods" && methodsBranch !== "worktables");

  const showWorktableBranches =
    selectedSection === "worktables" ||
    selectedSection === "carriers" ||
    selectedSection === "labware" ||
    selectedSection === "tools" ||
    (selectedSection === "methods" && methodsBranch === "worktables");

  const objectFilter = useMemo((): WorktableBranchId | ControlBarSection["objectFilter"] => {
    if (selectedSection === "carriers") return "carriers";
    if (selectedSection === "labware") return "labware";
    if (selectedSection === "tools") return "tools";
    if (selectedSection === "worktables" || (selectedSection === "methods" && methodsBranch === "worktables")) {
      return worktableBranch;
    }
    return activeSection.objectFilter;
  }, [activeSection.objectFilter, methodsBranch, selectedSection, worktableBranch]);

  const filteredObjects = useMemo(() => {
    if (!objectFilter) return [];
    return objectLibrary.filter((item) => {
      if (!objectLibraryMatchesSectionFilter(item.tags, item.componentRole || "", objectFilter)) return false;
      if (!normalizedSearch) return true;
      return normalizeSearchText([item.name, item.profile, item.componentRole, item.tags.join(" ")].join(" ")).includes(normalizedSearch);
    });
  }, [normalizedSearch, objectFilter, objectLibrary]);

  const visibleCommandTemplates = useMemo(() => {
    const scopedGroups =
      normalizedSearch || selectedSection === "modules"
        ? commandGroupsForView
        : [activeCommandsGroup];

    const entries = scopedGroups.flatMap((group) => group.commands.map((template) => ({ group, template })));
    if (!normalizedSearch) return entries;

    return entries.filter(({ group, template }) =>
      normalizeSearchText([group.label, template.name, template.operation, template.description, template.family].join(" ")).includes(normalizedSearch)
    );
  }, [activeCommandsGroup, commandGroupsForView, normalizedSearch, selectedSection]);

  const showCommands =
    selectedSection === "commands" ||
    selectedSection === "scripts" ||
    selectedSection === "modules" ||
    (selectedSection === "methods" && methodsBranch !== "worktables");

  const showObjects = showWorktableBranches;

  const showLiquidClasses = selectedSection === "liquidClasses";

  const liquidClassTemplate = controlBarCommandsGroupById("fca1").commands.find((command) => command.id === "fca1-set-liquid-class");

  return (
    <div className="control-bar">
      <div className="control-bar-section-tabs" role="tablist" aria-label="Control Bar sections">
        {CONTROL_BAR_SECTIONS.map((section) => (
          <button
            key={section.id}
            className={`control-bar-section-tab ${section.id === activeSection.id ? "is-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={section.id === activeSection.id}
            onClick={() => onSelectSection(section.id)}
          >
            {section.label}
          </button>
        ))}
      </div>

      <div className="control-bar-section-detail">
        {selectedSection === "methods" && (
          <TabRow
            tabs={METHODS_BRANCHES.map((branch) => ({ id: branch.id, label: branch.label }))}
            activeId={methodsBranch}
            onSelect={(id) => setMethodsBranch(id as MethodsBranchId)}
            ariaLabel="Methods branches"
          />
        )}

        {selectedSection === "modules" && (
          <TabRow
            tabs={MODULE_BRANCHES.map((branch) => ({
              id: branch.id,
              label: branch.label,
              count: controlBarCommandsGroupById(branch.id).commands.length
            }))}
            activeId={modulesBranch}
            onSelect={(id) => {
              const groupId = id as ModulesBranchId;
              setModulesBranch(groupId);
              onSelectCommandsGroup(groupId);
            }}
            ariaLabel="Module command groups"
          />
        )}

        {showCommandGroups && (
          <TabRow
            tabs={commandGroupTabs(commandGroupsForView)}
            activeId={selectedCommandsGroup}
            onSelect={(id) => onSelectCommandsGroup(id as ControlBarCommandsGroupId)}
            ariaLabel="Command groups"
          />
        )}

        {showWorktableBranches && (
          <TabRow
            tabs={WORKTABLE_BRANCHES.map((branch) => ({ id: branch.id, label: branch.label }))}
            activeId={worktableBranch}
            onSelect={(id) => setWorktableBranch(id as WorktableBranchId)}
            ariaLabel="Worktable object groups"
          />
        )}

        {showCommands && (
          <div className="control-bar-command-list">
            {visibleCommandTemplates.map(({ group, template }) => (
              <article
                key={template.id}
                className="control-bar-command-card"
                draggable
                onDragStart={(event) => onCommandDragStart(event, template)}
                title={`Drag ${template.name} into the script editor`}
              >
                <button type="button" className="control-bar-command-main" onClick={() => onAddCommand(template)}>
                  <strong>
                    {(normalizedSearch || selectedSection === "modules") && (
                      <span className="control-bar-command-group-label">{group.label}</span>
                    )}
                    {template.name}
                  </strong>
                  <span>{template.description}</span>
                </button>
              </article>
            ))}
            {!visibleCommandTemplates.length && <p className="muted">No commands match your search.</p>}
          </div>
        )}

        {showObjects && (
          <div className="control-bar-object-list" aria-label={`${worktableBranch} objects`}>
            {filteredObjects.map((item) => (
              <article
                className={`object-library-card ${item.id === activeObjectId ? "is-dragging" : ""} ${objectsEnabled ? "" : "is-disabled"}`}
                key={item.id}
                draggable={objectsEnabled}
                onDragStart={(event) => onObjectDragStart(event, item)}
                onDragEnd={onObjectDragEnd}
                title={objectsEnabled ? `Drag ${item.name} onto the 3D deck` : "Switch to Edit mode to place objects"}
                aria-disabled={!objectsEnabled}
              >
                <div className="object-library-swatch" style={{ backgroundColor: item.color }} />
                <div className="object-library-copy">
                  <strong>{item.name}</strong>
                  <span>{objectLibrarySummary(item)}</span>
                  <small>{item.tags.slice(0, 4).join(" · ")}</small>
                </div>
              </article>
            ))}
            {!filteredObjects.length && <p className="muted">No items match your search.</p>}
          </div>
        )}

        {showLiquidClasses && (
          <div className="control-bar-command-list" aria-label="Liquid classes">
            {CONTROL_BAR_LIQUID_CLASSES.filter((liquidClass) =>
              !normalizedSearch || normalizeSearchText(liquidClass).includes(normalizedSearch)
            ).map((liquidClass) => (
              <article key={liquidClass} className="control-bar-command-card">
                <button
                  type="button"
                  className="control-bar-command-main control-bar-command-main-full"
                  onClick={() => {
                    if (!liquidClassTemplate) return;
                    onAddCommand({
                      ...liquidClassTemplate,
                      defaults: {
                        ...liquidClassTemplate.defaults,
                        liquidClass
                      }
                    });
                  }}
                  title={`Add Set Liquid Class for ${liquidClass}`}
                >
                  <strong>{liquidClass}</strong>
                  <span>Set Liquid Class</span>
                </button>
              </article>
            ))}
            {!CONTROL_BAR_LIQUID_CLASSES.length && <p className="muted">No liquid classes available.</p>}
          </div>
        )}
      </div>
    </div>
  );
}
