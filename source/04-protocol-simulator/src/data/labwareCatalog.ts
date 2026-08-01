import type { ComponentRole, HardwareProfile, ObjectLibraryItem, WellShape } from "../types";

export type ModelVector3 = [number, number, number];

export type LabwareModelFit = "catalog-dimensions" | "fluent-dimensions" | "bounding-box";

export type LabwareGeometrySpec = {
  name: string;
  aliases: string[];
  physicalWidthMm: number;
  physicalDepthMm: number;
  physicalHeightMm: number;
  rows: number;
  cols: number;
  pitchXMm: number;
  pitchYMm: number;
  wellDiameterMm: number;
  wellDepthMm: number;
  maxVolumeUl: number;
  deadVolumeUl: number;
  shape: WellShape;
  modelAssetPath?: string;
  modelFallbackAssetPaths?: string[];
  /** Install-specific mesh GUIDs from ZEIA registration only — never bake host GUIDs into the static catalog. */
  meshGuidHints?: string[];
  meshNameHints?: string[];
  modelScale?: ModelVector3;
  modelRotationDeg?: ModelVector3;
  modelOffsetMm?: ModelVector3;
  modelFit?: LabwareModelFit;
  /** Exact ZEIA `.xcmp` FunctionalGroup when registered from import. */
  functionalGroup?: string;
};

export type ResolvedLabwareGeometry = LabwareGeometrySpec & {
  source: "catalog" | "inferred";
};

type ModelMetadataOptions = Pick<
  LabwareGeometrySpec,
  | "modelAssetPath"
  | "modelFallbackAssetPaths"
  | "meshGuidHints"
  | "meshNameHints"
  | "modelScale"
  | "modelRotationDeg"
  | "modelOffsetMm"
  | "modelFit"
>;

const GENERIC_COMPONENT_NAME = "Generic Fluent Component";
const FLUENT_MODEL_BASE_PATH = "/models/fluent/local";

/**
 * Soft well-grid templates for UI listing only — NOT product dim law.
 * Format grids only (no tip SKUs / filter / Source-Destination invent aliases).
 * Footprint/volume = 0. Never indexed into resolveLabwareGeometry (ZEIA only).
 * Mesh identity is ZEIA-only: `/models/fluent/local/<meshGuid>.glb`.
 */
const SOFT_WELL_TEMPLATES: LabwareGeometrySpec[] = [
  {
    name: "96 Well Flat",
    aliases: ["96 Well Plate", "96 Well Flat[001]"],
    physicalWidthMm: 0,
    physicalDepthMm: 0,
    physicalHeightMm: 0,
    rows: 8,
    cols: 12,
    pitchXMm: 9,
    pitchYMm: 9,
    wellDiameterMm: 0,
    wellDepthMm: 0,
    maxVolumeUl: 0,
    deadVolumeUl: 0,
    shape: "round",
    modelFit: "fluent-dimensions"
  },
  {
    name: "384 Well Plate",
    aliases: ["384 Well", "384 Well Flat"],
    physicalWidthMm: 0,
    physicalDepthMm: 0,
    physicalHeightMm: 0,
    rows: 16,
    cols: 24,
    pitchXMm: 4.5,
    pitchYMm: 4.5,
    wellDiameterMm: 0,
    wellDepthMm: 0,
    maxVolumeUl: 0,
    deadVolumeUl: 0,
    shape: "round",
    modelFit: "fluent-dimensions"
  },
  {
    name: "24 Well Plate",
    aliases: ["24 Well", "24 Well Plate[001]"],
    physicalWidthMm: 0,
    physicalDepthMm: 0,
    physicalHeightMm: 0,
    rows: 4,
    cols: 6,
    pitchXMm: 18,
    pitchYMm: 18,
    wellDiameterMm: 0,
    wellDepthMm: 0,
    maxVolumeUl: 0,
    deadVolumeUl: 0,
    shape: "square",
    modelFit: "fluent-dimensions"
  },
  {
    name: "Reagent Reservoir",
    aliases: ["Reservoir", "Reagent Reservoir[001]"],
    physicalWidthMm: 0,
    physicalDepthMm: 0,
    physicalHeightMm: 0,
    rows: 1,
    cols: 12,
    pitchXMm: 9,
    pitchYMm: 0,
    wellDiameterMm: 0,
    wellDepthMm: 0,
    maxVolumeUl: 0,
    deadVolumeUl: 0,
    shape: "square",
    modelFit: "fluent-dimensions"
  }
];

/** Minimal unknown placeholder only — indexed for resolve when ZEIA omits a name. */
const GENERIC_PLACEHOLDER: LabwareGeometrySpec = noWellSpec(
  GENERIC_COMPONENT_NAME,
  ["Unknown Fluent Component", "Worktable item"],
  120,
  80,
  20,
  { modelFit: "fluent-dimensions" }
);

/** UI listing = soft templates + generic. Not used as dim authority. */
const LABWARE_GEOMETRY_CATALOG: LabwareGeometrySpec[] = [...SOFT_WELL_TEMPLATES, GENERIC_PLACEHOLDER];


const GEOMETRY_BY_ALIAS = new Map<string, LabwareGeometrySpec>();
const GEOMETRY_BY_MESH_GUID = new Map<string, LabwareGeometrySpec>();
const GEOMETRY_BY_MESH_NAME_HINT = new Map<string, LabwareGeometrySpec>();
let zeiaLabwareCatalogEntryCount = 0;

function indexGeometrySpec(spec: LabwareGeometrySpec, options: { indexMeshHints?: boolean } = {}): void {
  const indexMeshHints = options.indexMeshHints !== false;
  [spec.name, ...spec.aliases].forEach((alias) => {
    GEOMETRY_BY_ALIAS.set(normalizeLabwareName(alias), spec);
  });
  if (!indexMeshHints) return;
  // Mesh identity only from import (labware_catalog.json / .xcmp) — not static invent.
  spec.meshGuidHints?.forEach((guid) => {
    const normalized = normalizeMeshGuid(guid);
    if (normalized) GEOMETRY_BY_MESH_GUID.set(normalized, spec);
  });
  spec.meshNameHints?.forEach((hint) => {
    GEOMETRY_BY_MESH_NAME_HINT.set(normalizeLabwareName(hint), spec);
  });
}

// Index generic placeholder only — soft well templates stay UI-only (no dim authority).
indexGeometrySpec(GENERIC_PLACEHOLDER, { indexMeshHints: false });

/**
 * Register labware geometry mined from an imported ZEIA labware_catalog.json or .xcmp.
 * Exact FluentControl object names stay as aliases; mesh GUID/name hints only from import.
 */
export function registerLabwareGeometrySpec(spec: LabwareGeometrySpec): void {
  const existing = GEOMETRY_BY_ALIAS.get(normalizeLabwareName(spec.name));
  const merged: LabwareGeometrySpec = existing
    ? {
        ...existing,
        ...spec,
        aliases: Array.from(new Set([...(existing.aliases || []), ...(spec.aliases || []), spec.name, existing.name])),
        meshGuidHints: Array.from(new Set([...(existing.meshGuidHints || []), ...(spec.meshGuidHints || [])])),
        // Import mesh names only — never reattach static invent hints.
        meshNameHints: Array.from(new Set([...(spec.meshNameHints || [])])),
        modelFallbackAssetPaths: Array.from(
          new Set([...(existing.modelFallbackAssetPaths || []), ...(spec.modelFallbackAssetPaths || [])])
        ),
        functionalGroup: spec.functionalGroup || existing.functionalGroup
      }
    : { ...spec, aliases: Array.from(new Set([...(spec.aliases || []), spec.name])) };
  indexGeometrySpec(merged, { indexMeshHints: true });
}

export type ZeiaLabwareCatalogEntry = {
  name?: string;
  aliases?: string[];
  /** Component GUID from ZEIA — identity only, not a mesh/GLB stem. */
  guid?: string;
  /** Primary WorktableMesh GUID (install/ZEIA-correct GLB key). */
  mesh_guid?: string | null;
  mesh_guids?: string[] | null;
  mesh_names?: string[] | null;
  physical_width_mm?: number | null;
  physical_depth_mm?: number | null;
  physical_height_mm?: number | null;
  rows?: number | null;
  cols?: number | null;
  pitch_x_mm?: number | null;
  pitch_y_mm?: number | null;
  well_diameter_mm?: number | null;
  well_depth_mm?: number | null;
  well_shape?: "round" | "square" | "none" | string | null;
  max_volume_ul?: number | null;
  pipettable?: Record<string, unknown> | null;
  grip?: {
    allowed_modes?: Record<string, string[]> | null;
    force?: string | number | null;
  } | null;
  site_templates?: Array<{
    index?: number | null;
    guid?: string;
    location_group_name?: string | null;
    type_name?: string | null;
    site_kind?: string | null;
  }> | null;
  compatible_components?: Array<{ guid?: string; name?: string }> | null;
  compatible_component_names?: string[] | null;
  compatible_component_guids?: string[] | null;
  /** Exact ZEIA `.xcmp` FunctionalGroup (e.g. Labware.Microplate, Carrier.Miscellaneous). */
  functional_group?: string | null;
};

export function registerLabwareCatalogEntries(entries: ZeiaLabwareCatalogEntry[]): number {
  let registered = 0;
  for (const entry of entries) {
    const name = String(entry.name || "").trim();
    if (!name) continue;
    // Exact ZEIA name wins. Never fill missing dims/wells from static SBS soft templates.
    const aliases = Array.from(
      new Set([name, ...(entry.aliases || []).map((value) => String(value || "").trim()).filter(Boolean)])
    );
    const meshGuids = meshGuidsFromCatalogEntry(entry);
    const primaryMeshGuid = meshGuids[0] || "";
    const meshNames = (entry.mesh_names || [])
      .map((value) => String(value || "").trim())
      .filter(Boolean);
    const width = Number(entry.physical_width_mm) || 0;
    const depth = Number(entry.physical_depth_mm) || 0;
    const height = Number(entry.physical_height_mm) || 0;
    const hasImportDims = width > 0 || depth > 0 || height > 0;
    const rows = Number(entry.rows) || 1;
    const cols = Number(entry.cols) || 1;
    const hasWellGrid = rows > 1 || cols > 1 || Number(entry.well_diameter_mm) > 0 || Number(entry.well_depth_mm) > 0;
    const importShape = String(entry.well_shape || "").trim();
    const shape =
      importShape === "round" || importShape === "square" || importShape === "none"
        ? importShape
        : hasWellGrid
          ? "round"
          : "none";
    registerLabwareGeometrySpec({
      name,
      aliases,
      physicalWidthMm: width,
      physicalDepthMm: depth,
      physicalHeightMm: height,
      rows,
      cols,
      pitchXMm: Number(entry.pitch_x_mm) || 0,
      pitchYMm: Number(entry.pitch_y_mm) || 0,
      wellDiameterMm: Number(entry.well_diameter_mm) || 0,
      wellDepthMm: Number(entry.well_depth_mm) || 0,
      maxVolumeUl: Number(entry.max_volume_ul) || 0,
      deadVolumeUl: 0,
      shape,
      modelAssetPath: primaryMeshGuid ? fluentAsset(primaryMeshGuid) : undefined,
      modelFallbackAssetPaths: [],
      meshGuidHints: meshGuids.length ? meshGuids : undefined,
      meshNameHints: meshNames.length ? meshNames : undefined,
      modelFit: primaryMeshGuid || hasImportDims ? "fluent-dimensions" : "fluent-dimensions",
      functionalGroup: String(entry.functional_group || "").trim() || undefined
    });
    registered += 1;
  }
  return registered;
}

/** True after labware_catalog.json or .xcmp definition registration (not static invent). */
export function hasZeiaLabwareCatalog(): boolean {
  return zeiaLabwareCatalogEntryCount > 0;
}

export function zeiaLabwareCatalogEntryTotal(): number {
  return zeiaLabwareCatalogEntryCount;
}

function meshGuidsFromCatalogEntry(entry: ZeiaLabwareCatalogEntry): string[] {
  const values = [
    ...(Array.isArray(entry.mesh_guids) ? entry.mesh_guids : []),
    entry.mesh_guid || ""
  ];
  const out: string[] = [];
  for (const value of values) {
    const guid = normalizeMeshGuid(String(value || ""));
    if (guid && !out.includes(guid)) out.push(guid);
  }
  return out;
}

export function registerLabwareCatalogFromDefinitions(
  definitions: Array<{
    catalogName?: string;
    componentName?: string;
    dimensionMm?: { x?: number; y?: number; z?: number } | null;
    meshGuid?: string;
    meshGuids?: string[];
    meshNames?: string[];
    rows?: number;
    cols?: number;
    pitchXMm?: number;
    pitchYMm?: number;
    wellDiameterMm?: number;
    wellDepthMm?: number;
    wellShape?: string;
    maxVolumeUl?: number;
    functionalGroup?: string;
  }>
): number {
  const registered = registerLabwareCatalogEntries(
    definitions.map((definition) => ({
      name: definition.componentName || definition.catalogName || "",
      aliases: [definition.componentName || "", definition.catalogName || ""].filter(Boolean),
      mesh_guid: definition.meshGuid || null,
      mesh_guids: definition.meshGuids || null,
      mesh_names: definition.meshNames || null,
      physical_width_mm: definition.dimensionMm?.x,
      physical_depth_mm: definition.dimensionMm?.y,
      physical_height_mm: definition.dimensionMm?.z,
      rows: definition.rows,
      cols: definition.cols,
      pitch_x_mm: definition.pitchXMm,
      pitch_y_mm: definition.pitchYMm,
      well_diameter_mm: definition.wellDiameterMm,
      well_depth_mm: definition.wellDepthMm,
      well_shape: definition.wellShape,
      max_volume_ul: definition.maxVolumeUl,
      functional_group: definition.functionalGroup || null
    }))
  );
  zeiaLabwareCatalogEntryCount += registered;
  return registered;
}

export function registerLabwareCatalogPayload(payload: unknown): number {
  if (!payload || typeof payload !== "object") return 0;
  const record = payload as { entries?: ZeiaLabwareCatalogEntry[]; schema_version?: string };
  if (!Array.isArray(record.entries)) return 0;
  const registered = registerLabwareCatalogEntries(record.entries);
  zeiaLabwareCatalogEntryCount += registered;
  return registered;
}

export function resolveLabwareGeometry(catalogName: string, label: string, meshGuid = "", meshName = ""): ResolvedLabwareGeometry {
  const candidates = [label, catalogName].filter(Boolean);
  for (const candidate of candidates) {
    const match = GEOMETRY_BY_ALIAS.get(normalizeLabwareName(candidate));
    if (match) return { ...match, source: "catalog" };
  }

  const meshMatch = resolveGeometryFromMesh(meshGuid, meshName);
  if (meshMatch) return { ...meshMatch, source: "catalog" };

  return { ...inferGeometry(catalogName || label), source: "inferred" };
}

export function resolveModelAssetPath(input: { catalogName?: string; label?: string; meshGuid?: string; meshName?: string }): string {
  // Prefer the worktable/ZEIA mesh GUID when present (install-correct). Never bake host GUIDs into the static catalog.
  const meshGuid = normalizeMeshGuid(input.meshGuid || "");
  if (meshGuid) return fluentAsset(meshGuid);

  const geometry = resolveLabwareGeometry(input.catalogName || "", input.label || "", input.meshGuid || "", input.meshName || "");
  if (geometry.modelAssetPath) return geometry.modelAssetPath;
  // meshGuidHints are only populated from ZEIA registration, not the static catalog.
  const hintedGuid = geometry.meshGuidHints?.map(normalizeMeshGuid).find(Boolean);
  if (hintedGuid) return fluentAsset(hintedGuid);
  return "";
}

export function modelAssetPathForMeshGuid(meshGuid: string): string {
  const guid = normalizeMeshGuid(meshGuid);
  return guid ? fluentAsset(guid) : "";
}

export function inferCatalogNameFromLabel(label: string): string {
  const direct = GEOMETRY_BY_ALIAS.get(normalizeLabwareName(label));
  if (direct) return direct.name;

  // Exact Fluent type from the instance label only. Never invent stock names
  // (CapHolder_long_44mm, Falcon runners, 61mm Nest, Resolvex, …) — those come
  // from ZEIA labware_catalog.json registration.
  const bare = stripFluentInstanceSuffix(label);
  return bare || label.trim() || GENERIC_COMPONENT_NAME;
}


export function listCatalogObjectLibraryItems(): ObjectLibraryItem[] {
  return LABWARE_GEOMETRY_CATALOG.map(catalogObjectLibraryItem);
}

export function searchCatalogObjectLibraryItems(query: string, limit = 40): ObjectLibraryItem[] {
  const normalizedQuery = normalizeLabwareName(query);
  const items = listCatalogObjectLibraryItems();
  if (!normalizedQuery) return items.slice(0, limit);

  return items
    .map((item) => ({ item, score: catalogSearchScore(item, normalizedQuery) }))
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score || a.item.name.localeCompare(b.item.name))
    .slice(0, limit)
    .map(({ item }) => item);
}

export function resolveCatalogObjectLibraryItem(idOrName: string): ObjectLibraryItem | null {
  const normalized = normalizeLabwareName(idOrName).replace(/\s+/g, "-");
  return (
    listCatalogObjectLibraryItems().find((item) => item.id === normalized || normalizeLabwareName(item.name) === normalizeLabwareName(idOrName)) ||
    null
  );
}

export function listLabwareGeometryCatalog(): ResolvedLabwareGeometry[] {
  // Soft templates are UI heuristics only — never report as authoritative "catalog" dims.
  return LABWARE_GEOMETRY_CATALOG.map((spec) => ({
    ...spec,
    source: spec.name === GENERIC_COMPONENT_NAME ? ("catalog" as const) : ("inferred" as const)
  }));
}

function catalogObjectLibraryItem(spec: LabwareGeometrySpec): ObjectLibraryItem {
  const tags = catalogTagsForSpec(spec);
  return {
    id: normalizeLabwareName(spec.name).replace(/\s+/g, "-"),
    name: spec.name,
    profile: hardwareProfileForCatalogSpec(spec),
    componentRole: componentRoleForCatalogSpec(spec),
    modelAssetPath: spec.modelAssetPath || spec.modelFallbackAssetPaths?.[0] || "",
    tags
  };
}

function catalogTagsForSpec(spec: LabwareGeometrySpec): string[] {
  const text = normalizeLabwareName([spec.name, ...spec.aliases].join(" "));
  const group = normalizeLabwareName(spec.functionalGroup || "");
  const profile = hardwareProfileForCatalogSpec(spec);
  const tags = new Set<string>();
  tags.add(spec.shape === "none" ? "object" : "labware");
  if (spec.rows && spec.cols) tags.add(`${spec.rows}x${spec.cols}`);
  if (spec.maxVolumeUl > 0) tags.add(`${spec.maxVolumeUl}ul`);
  if (group) tags.add(group);
  if (profile === "tip-box") tags.add("tips");
  if (profile === "tube-holder" || textIncludesExactTubePhrase(text)) tags.add("tube rack");
  if (profile === "reservoir") tags.add("reservoir");
  return Array.from(tags);
}


function catalogSearchScore(item: ObjectLibraryItem, normalizedQuery: string): number {
  const haystack = normalizeLabwareName([item.name, item.profile, item.componentRole || "", ...item.tags].join(" "));
  const terms = normalizedQuery.split(" ").filter(Boolean);
  if (!terms.length) return 1;
  let score = 0;
  if (haystack === normalizedQuery) score += 1000;
  if (haystack.startsWith(normalizedQuery)) score += 500;
  if (haystack.includes(normalizedQuery)) score += 250;
  terms.forEach((term) => {
    if (haystack.includes(term)) score += 50;
  });
  if (item.modelAssetPath) score += 5;
  return score;
}

/**
 * Map ZEIA FunctionalGroup → HardwareProfile.
 * Tube runners/holders sit under Carrier.Miscellaneous — exact object-name phrases only.
 * No filter/DWP/tip/adapter/falcon keyword invent.
 */
export function hardwareProfileFromZeia(input: {
  functionalGroup?: string;
  nameText?: string;
  shape?: WellShape;
}): HardwareProfile {
  const name = normalizeLabwareName(input.nameText || "");
  if (textIncludesExactTubePhrase(name)) return "tube-holder";

  const group = normalizeLabwareName(input.functionalGroup || "");
  if (group) {
    if (group.includes("diti")) return "tip-box";
    if (group.includes("trough")) return "reservoir";
    if (group.includes("wash and waste") || (group.includes("wash") && group.includes("waste"))) return "waste";
    if (group.includes("microplate") || group.includes("deep well")) return "plate";
    if (group.startsWith("labware")) return "generic";
    if (group.includes("hotel")) return "adapter";
    if (group.includes("device") || group.includes("3rd party")) return "device";
    if (group.includes("deck")) return "deck";
    if (group.includes("nest")) return "nest";
    if (group.includes("grid segment") || group.startsWith("carrier")) return "carrier";
    if (group.includes("base unit")) return "structural";
  }

  return input.shape === "none" ? "unknown-component" : "generic";
}

function textIncludesExactTubePhrase(text: string): boolean {
  return text.includes("tube holder") || text.includes("tube runner");
}

function hardwareProfileForCatalogSpec(spec: LabwareGeometrySpec): HardwareProfile {
  return hardwareProfileFromZeia({
    functionalGroup: spec.functionalGroup,
    nameText: [spec.name, ...spec.aliases].join(" "),
    shape: spec.shape
  });
}


function componentRoleForCatalogSpec(spec: LabwareGeometrySpec): ComponentRole {
  const profile = hardwareProfileForCatalogSpec(spec);
  if (profile === "tip-box") return "tip-box";
  if (profile === "tube-holder") return "tube-rack";
  if (profile === "cap-holder") return "cap-holder";
  if (profile === "adapter") return "adapter";
  if (profile === "carrier") return "carrier";
  if (profile === "nest") return "nest";
  if (profile === "device") return "device";
  if (profile === "structural" || profile === "deck") return "structural";
  if (profile === "waste") return "waste";
  if (profile === "wash-station") return "wash-station";
  if (profile === "reservoir") return "reservoir";
  return spec.shape === "none" ? "unknown" : "labware";
}

function noWellSpec(
  name: string,
  aliases: string[],
  physicalWidthMm: number,
  physicalDepthMm: number,
  physicalHeightMm: number,
  modelOptions: ModelMetadataOptions = {}
): LabwareGeometrySpec {
  return {
    name,
    aliases,
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
    shape: "none",
    ...modelOptions
  };
}

function resolveGeometryFromMesh(meshGuid: string, meshName: string): LabwareGeometrySpec | null {
  const normalizedGuid = normalizeMeshGuid(meshGuid);
  if (normalizedGuid) {
    const byGuid = GEOMETRY_BY_MESH_GUID.get(normalizedGuid);
    if (byGuid) return byGuid;
  }

  const normalizedName = normalizeLabwareName(meshName);
  if (!normalizedName) return null;

  // Exact import mesh-name hint only — no fuzzy static meshNameHints invent.
  return GEOMETRY_BY_MESH_NAME_HINT.get(normalizedName) || null;
}

function inferGeometry(name: string): LabwareGeometrySpec {
  const lower = normalizeLabwareName(name);
  const exact = GEOMETRY_BY_ALIAS.get(lower);
  if (exact) return { ...exact };

  const explicit = lower.match(/(\d+)x(\d+)/);
  if (explicit) {
    const rows = Number(explicit[1]);
    const cols = Number(explicit[2]);
    const isTube = lower.includes("tube holder") || lower.includes("tube runner");
    const pitchXMm = isTube ? inferTubePitchMm(lower) : 0;
    const bareName = stripFluentInstanceSuffix(name) || name.trim() || `Inferred ${rows}x${cols} Labware`;
    // Grid shape only — no SBS footprint / volume invent when ZEIA thin.
    return {
      name: bareName,
      aliases: [],
      physicalWidthMm: 0,
      physicalDepthMm: 0,
      physicalHeightMm: 0,
      rows,
      cols,
      pitchXMm,
      pitchYMm: rows > 1 ? pitchXMm : 0,
      wellDiameterMm: 0,
      wellDepthMm: 0,
      maxVolumeUl: 0,
      deadVolumeUl: 0,
      shape: isTube ? "round" : "square",
      modelAssetPath: inferredModelAssetPath(lower),
      modelFit: "fluent-dimensions"
    };
  }

  // No stock-name invention (CapHolder / Falcon / Nest / Resolvex / plates).
  // Missing ZEIA labware_catalog → generic placeholder with the given label.
  const bareName = stripFluentInstanceSuffix(name) || name.trim() || GENERIC_COMPONENT_NAME;
  const generic = catalogFallback(GENERIC_COMPONENT_NAME);
  return { ...generic, name: bareName, aliases: [] };
}

function catalogFallback(name: string): LabwareGeometrySpec {
  return (
    GEOMETRY_BY_ALIAS.get(normalizeLabwareName(name)) ||
    GEOMETRY_BY_ALIAS.get(normalizeLabwareName(GENERIC_COMPONENT_NAME)) ||
    GENERIC_PLACEHOLDER
  );
}

function stripFluentInstanceSuffix(value: string): string {
  return value.replace(/\[[^\]]+\]\s*$/, "").trim();
}

function inferTubePitchMm(text: string): number {
  if (text.includes("15ml")) return 22.95;
  if (text.includes("50ml")) return 37;
  return 18;
}

function inferredModelAssetPath(_normalizedText: string): string {
  // No stock /models/labware|devices meshes — ZEIA mesh GUID via fluentAsset only.
  return "";
}


function fluentAsset(meshGuid: string): string {
  const guid = normalizeMeshGuid(meshGuid);
  return guid ? `${FLUENT_MODEL_BASE_PATH}/${guid}.glb` : "";
}

function normalizeMeshGuid(value: string): string {
  const match = value.toLowerCase().match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);
  return match?.[0] || "";
}

function normalizeLabwareName(value: string): string {
  return value
    .replace(/\[[^\]]+\]\s*$/, "")
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/[,]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
