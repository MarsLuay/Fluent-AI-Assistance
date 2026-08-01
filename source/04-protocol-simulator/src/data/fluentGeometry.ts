import type { FluentTransformModel, LabwareDefinitionModel, Matrix3Model, SourceArtifact, Vector3Model, WellShape } from "../types";

export type FluentComponentRole =
  | "labware"
  | "tip-box"
  | "tube-rack"
  | "carrier"
  | "nest"
  | "adapter"
  | "device"
  | "structural"
  | "deck"
  | "waste"
  | "tool"
  | "unknown";

export type FluentMeshMetadata = {
  guid: string;
  name: string;
  sourcePath: string;
  payloadSize: number;
  decodedSize: number | null;
  base64Length: number;
  version: string;
  dataVersion: string;
  checksum: string;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  modelAssetPath: string;
};

export type FluentGeometryRecord = {
  label: string;
  catalogName: string;
  role: FluentComponentRole;
  functionalGroup: string;
  footprint: string;
  componentName: string;
  location: string;
  position: number | null;
  meshGuid?: string;
  meshName?: string;
  meshSourcePath?: string;
  meshPayloadSize?: number;
  meshDecodedSize?: number | null;
  unitScaleToMm?: number;
  unitScaleSource?: string;
  nativeUnit?: string;
  modelAssetPath?: string;
  wellSites?: FluentWellSiteModel[];
  transform: FluentTransformModel;
};

export type FluentWellSiteModel = {
  index: number;
  row: number;
  col: number;
  positionMm: Vector3Model;
  source: "fluent-pipettable" | "fluent-arrangement";
  siteGuid?: string;
  wellShape?: WellShape;
  diameterMm?: number;
  depthMm?: number;
};

export type FluentGeometryBundle = {
  labware: FluentGeometryRecord[];
  labwareDefinitions: LabwareDefinitionModel[];
  meshes: FluentMeshMetadata[];
  metadata: {
    name?: string;
    context?: string;
    worktableName?: string;
  };
  hardware: {
    connectors: number;
    meshes: number;
  };
  warnings: string[];
};

type Transform = {
  position: Vector3Model;
  orientation: Matrix3Model;
};

type FluentMeshReference = {
  guid: string;
  name: string;
};

type ComponentTemplate = {
  guid: string;
  name: string;
  functionalGroup: string;
  footprint: string;
  role: FluentComponentRole;
  dimension: Vector3Model | null;
  renderer: string;
  meshReference: FluentMeshReference | null;
  meshReferences: FluentMeshReference[];
  mesh: FluentMeshMetadata | null;
  wellSites: FluentWellSiteModel[];
  wellShape?: WellShape;
  wellDiameterMm?: number;
  wellDepthMm?: number;
  arrangements: Map<string, ArrangementTemplate>;
  firstArrangement: ArrangementTemplate | null;
};

type ArrangementTemplate = {
  id: string;
  positionInParent: Vector3Model;
  siteOffsets: Map<number, Vector3Model>;
  siteTemplateGuids: Map<number, string>;
  sitesInX: number;
  sitesInY: number;
  sitesInZ: number;
  spacing: Vector3Model;
};

type FluentWellGeometry = {
  wellShape?: WellShape;
  wellDiameterMm?: number;
  wellDepthMm?: number;
};

type ConnectorTemplate = {
  guid: string;
  componentGuid: string;
  siteGuid: string;
  transform: Transform;
};

type SiteTemplate = {
  guid: string;
  dimension: Vector3Model | null;
  orientation: Matrix3Model;
  locationGroupName: string;
};

type WorkspaceDocument = {
  artifact: SourceArtifact;
  document: Document;
  guid: string;
  name: string;
};

type WorktableHints = {
  guids: Set<string>;
  names: Set<string>;
};

const IDENTITY_MATRIX: Matrix3Model = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1]
];

const ZERO_VECTOR: Vector3Model = { x: 0, y: 0, z: 0 };
const EMPTY_GUID = "00000000-0000-0000-0000-000000000000";
const GUID_IN_TEXT_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const MAX_WELL_SITE_COUNT = 384;

export function parseFluentGeometryArtifacts(artifacts: SourceArtifact[]): FluentGeometryBundle {
  const xmlArtifacts = artifacts.filter(isWorktableGeometryArtifact);
  const empty: FluentGeometryBundle = {
    labware: [],
    labwareDefinitions: [],
    meshes: [],
    metadata: {},
    hardware: { connectors: 0, meshes: 0 },
    warnings: []
  };
  if (!xmlArtifacts.length) return empty;

  const warnings: string[] = [];
  const documents: Array<{ artifact: SourceArtifact; document: Document }> = [];

  xmlArtifacts.forEach((artifact) => {
    const document = parseXml(artifact);
    if (document) {
      documents.push({ artifact, document });
      return;
    }
    warnings.push(`Skipped unreadable Fluent worktable XML: ${artifact.name}`);
  });

  const components = new Map<string, ComponentTemplate>();
  const connectors = new Map<string, ConnectorTemplate>();
  const sites = new Map<string, SiteTemplate>();
  const meshes = new Map<string, FluentMeshMetadata>();
  const workspaceDocuments: WorkspaceDocument[] = [];

  documents.forEach(({ artifact, document }) => {
    const lower = artifact.name.toLowerCase();
    if (lower.endsWith(".xcmp")) {
      const component = parseComponentTemplate(document);
      if (component?.guid) components.set(normalizeGuid(component.guid), component);
      return;
    }
    if (lower.endsWith(".xcon")) {
      const connector = parseConnectorTemplate(document);
      if (connector?.guid) connectors.set(normalizeGuid(connector.guid), connector);
      return;
    }
    if (lower.endsWith(".xsit")) {
      const site = parseSiteTemplate(document);
      if (site?.guid) sites.set(normalizeGuid(site.guid), site);
      return;
    }
    if (lower.endsWith(".xmsh")) {
      const mesh = parseMeshMetadata(document, artifact);
      if (mesh?.guid) meshes.set(normalizeGuid(mesh.guid), mesh);
      return;
    }
    if (lower.endsWith(".xwsp")) workspaceDocuments.push(workspaceDocumentFor(artifact, document));
  });

  attachMeshesToComponents(components, meshes);

  if (!workspaceDocuments.length && (components.size || connectors.size || sites.size)) {
    warnings.push(
      "Found Fluent worktable component/site/connector XML, but no .xwsp workspace file was loaded. The deck may fall back to synthetic placement."
    );
  }

  const records: FluentGeometryRecord[] = [];
  const selectedWorkspaceDocuments = selectWorkspaceDocuments(workspaceDocuments, artifacts, warnings);
  selectedWorkspaceDocuments.forEach(({ artifact, document }) => {
    records.push(...parseWorkspace(document, artifact, components, connectors, sites));
  });

  const dedupedRecords = dedupeGeometryRecords(records);
  if (workspaceDocuments.length && !dedupedRecords.length) {
    warnings.push("Loaded Fluent workspace XML, but no connected worktable components were resolved.");
  }

  const roleCounts = countRecordsByRole(dedupedRecords);
  const unknownCount = roleCounts.get("unknown") || 0;
  if (unknownCount) {
    warnings.push(`${unknownCount} Fluent worktable component(s) could not be classified and will use generic no-well geometry.`);
  }

  const unmatchedMeshReferenceCount = Array.from(components.values()).filter((component) => component.meshReference && !component.mesh).length;
  if (unmatchedMeshReferenceCount) {
    warnings.push(
      `${unmatchedMeshReferenceCount} Fluent component mesh reference${unmatchedMeshReferenceCount === 1 ? "" : "s"} could not be matched to loaded .xmsh metadata.`
    );
  }

  const worktableName = firstString(selectedWorkspaceDocuments.map(({ name }) => name)) || firstString(workspaceDocuments.map(({ name }) => name));
  const meshList = Array.from(meshes.values()).sort((a, b) => a.name.localeCompare(b.name) || a.guid.localeCompare(b.guid));
  return {
    labware: dedupedRecords,
    labwareDefinitions: labwareDefinitionsForComponents(components),
    meshes: meshList,
    metadata: {
      name: worktableName,
      context: firstString(workspaceDocuments.map(({ artifact }) => artifact.source)),
      worktableName
    },
    hardware: {
      connectors: connectors.size,
      meshes: meshes.size
    },
    warnings
  };
}

function labwareDefinitionsForComponents(components: Map<string, ComponentTemplate>): LabwareDefinitionModel[] {
  return Array.from(components.values())
    .filter((template) => Boolean(template.name?.trim()))
    .map((template) => {
      const arrangement = template.firstArrangement;
      const meshGuids = template.meshReferences.map((ref) => normalizeGuid(ref.guid)).filter(isRealGuid);
      const meshNames = template.meshReferences.map((ref) => ref.name).filter(Boolean);
      const primaryMesh = template.mesh?.guid || template.meshReference?.guid || meshGuids[0] || "";
      const pipettableSites = template.wellSites.filter((site) => site.source === "fluent-pipettable");
      const gridSites = pipettableSites.length ? pipettableSites : template.wellSites;
      const pipettableRows = gridSites.length
        ? Math.max(...gridSites.map((site) => site.row)) + 1
        : undefined;
      const pipettableCols = gridSites.length
        ? Math.max(...gridSites.map((site) => site.col)) + 1
        : undefined;
      const pitchXMm =
        pipettableSites.length >= 2
          ? Math.abs((pipettableSites.find((site) => site.col === 1)?.positionMm.x || 0) - (pipettableSites[0]?.positionMm.x || 0))
          : arrangement?.spacing.x;
      const pitchYMm =
        pipettableSites.length >= 2
          ? Math.abs((pipettableSites.find((site) => site.row === 1)?.positionMm.y || 0) - (pipettableSites[0]?.positionMm.y || 0))
          : arrangement?.spacing.y;
      return {
        catalogName: catalogNameForRecord(template.name, template, template.role),
        componentName: template.name,
        role: template.role,
        functionalGroup: template.functionalGroup || undefined,
        dimensionMm: template.dimension,
        meshGuid: primaryMesh || undefined,
        meshGuids: meshGuids.length ? meshGuids : undefined,
        meshNames: meshNames.length ? meshNames : undefined,
        rows: pipettableRows || (arrangement ? Math.max(1, arrangement.sitesInY) : undefined),
        cols: pipettableCols || (arrangement ? Math.max(1, arrangement.sitesInX) : undefined),
        pitchXMm,
        pitchYMm,
        wellShape: template.wellShape,
        wellDiameterMm: template.wellDiameterMm,
        wellDepthMm: template.wellDepthMm,
        wellSites: template.wellSites
      };
    })
    .sort((a, b) => a.catalogName.localeCompare(b.catalogName) || a.componentName.localeCompare(b.componentName));
}

function workspaceDocumentFor(artifact: SourceArtifact, document: Document): WorkspaceDocument {
  return {
    artifact,
    document,
    guid: normalizeGuid(stemForPath(artifact.path || artifact.name)) || normalizeGuid(textOf(document, "GUID")),
    name: textOf(document, "ObjectName")
  };
}

function selectWorkspaceDocuments(
  workspaceDocuments: WorkspaceDocument[],
  artifacts: SourceArtifact[],
  warnings: string[]
): WorkspaceDocument[] {
  if (workspaceDocuments.length <= 1) return workspaceDocuments;

  const hints = worktableHintsFromArtifacts(artifacts);
  const byGuid = workspaceDocuments.filter((workspace) => hints.guids.has(workspace.guid));
  const selected = byGuid.length ? byGuid : workspaceDocuments.filter((workspace) => hints.names.has(canonicalText(workspace.name)));
  if (!selected.length) return workspaceDocuments;

  const selectedNames = selected.map((workspace) => workspace.name || workspace.guid).filter(Boolean).join(", ");
  warnings.push(`Using Fluent workspace ${selectedNames}; ignored ${workspaceDocuments.length - selected.length} alternate workspace(s) from the source ZEIA.`);
  return selected;
}

function worktableHintsFromArtifacts(artifacts: SourceArtifact[]): WorktableHints {
  const hints: WorktableHints = { guids: new Set(), names: new Set() };
  artifacts.forEach((artifact) => {
    if (!looksLikeJsonArtifact(artifact)) return;
    const payload = parseJsonRecord(artifact.text);
    if (payload) collectWorktableHints(payload, hints, false);
  });
  return hints;
}

function collectWorktableHints(value: unknown, hints: WorktableHints, insideWorktable: boolean): void {
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach((item) => collectWorktableHints(item, hints, insideWorktable));
    return;
  }

  Object.entries(value as Record<string, unknown>).forEach(([key, child]) => {
    const normalizedKey = canonicalText(key);
    const childInsideWorktable = insideWorktable || normalizedKey === "worktable";
    if (typeof child === "string") {
      const guid = normalizeGuid(child);
      if (guid && (childInsideWorktable || normalizedKey.includes("worktable"))) hints.guids.add(guid);
      if ((childInsideWorktable && (normalizedKey === "name" || normalizedKey === "worktable name")) || normalizedKey === "worktable") {
        const name = canonicalText(child);
        if (name && !guid) hints.names.add(name);
      }
      return;
    }
    collectWorktableHints(child, hints, childInsideWorktable);
  });
}

function looksLikeJsonArtifact(artifact: SourceArtifact): boolean {
  const lowerName = `${artifact.name} ${artifact.path || ""}`.toLowerCase();
  return artifact.kind === "protocol-ir" || artifact.kind === "metadata" || lowerName.endsWith(".json");
}

function parseJsonRecord(text: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function isWorktableGeometryName(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".xwsp") || lower.endsWith(".xcmp") || lower.endsWith(".xcon") || lower.endsWith(".xsit") || lower.endsWith(".xmsh");
}

function isWorktableGeometryArtifact(artifact: SourceArtifact): boolean {
  return (
    artifact.kind === "worktable-geometry" ||
    artifact.kind === "worktable-mesh" ||
    isWorktableGeometryName(artifact.name) ||
    isWorktableGeometryName(artifact.path || "")
  );
}

function parseXml(artifact: SourceArtifact): Document | null {
  const document = new DOMParser().parseFromString(stripByteOrderMark(artifact.text), "application/xml");
  const parserErrors =
    typeof document.querySelector === "function"
      ? document.querySelector("parsererror")
      : document.getElementsByTagName("parsererror")[0] || document.getElementsByTagNameNS?.("*", "parsererror")[0];
  if (parserErrors) return null;
  return document;
}

function parseComponentTemplate(document: Document): ComponentTemplate | null {
  const template = firstByLocalName(document, "CarrierOrLabwareTemplate");
  if (!template) return null;

  const arrangements = new Map<string, ArrangementTemplate>();
  directChildren(firstDirectChild(template, "Arrangements"), "ArrangementTemplate").forEach((arrangementElement, index) => {
    const arrangement = parseArrangementTemplate(arrangementElement, index);
    arrangements.set(normalizeGuid(arrangement.id), arrangement);
  });

  const name = directText(template, "Name") || textOf(document, "ObjectName");
  const functionalGroup = directText(template, "FunctionalGroup");
  const footprint = directText(template, "FootPrint");
  const renderer = directText(template, "Renderer");
  const role = classifyComponentRole(name, functionalGroup, footprint);
  const meshReferences = parseComponentMeshReferences(document, renderer);
  const meshReference = meshReferences[0] || null;

  const firstArrangement = arrangements.values().next().value ?? null;
  const wellSites = parseTemplateWellSites(template, arrangements);
  const wellGeometry = fluentWellGeometryFromSites(wellSites);
  return {
    guid: textOf(template, "GUID") || textOf(document, "ObjectName"),
    name,
    functionalGroup,
    footprint,
    role,
    dimension: parseDirectVector(template, "Dimension"),
    renderer,
    meshReference,
    meshReferences,
    mesh: null,
    wellSites,
    wellShape: wellGeometry.wellShape,
    wellDiameterMm: wellGeometry.wellDiameterMm,
    wellDepthMm: wellGeometry.wellDepthMm,
    arrangements,
    firstArrangement
  };
}

function parseArrangementTemplate(element: Element, index: number): ArrangementTemplate {
  return {
    id: directText(element, "InternalID") || `arrangement-${index}`,
    positionInParent: parseDirectVector(element, "PositionInParent") || ZERO_VECTOR,
    siteOffsets: parseVectorMap(firstDirectChild(element, "SiteOffsets")),
    siteTemplateGuids: parseGuidMap(firstDirectChild(element, "SiteTemplateIdentifiers")),
    sitesInX: numberText(element, "SitesInX") || 1,
    sitesInY: numberText(element, "SitesInY") || 1,
    sitesInZ: numberText(element, "SitesInZ") || 1,
    spacing: {
      x: numberText(element, "SiteSpacingInX") || 0,
      y: numberText(element, "SiteSpacingInY") || 0,
      z: numberText(element, "SiteSpacingInZ") || 0
    }
  };
}

function parseConnectorTemplate(document: Document): ConnectorTemplate | null {
  const template = firstByLocalName(document, "ConnectorTemplate");
  if (!template) return null;

  return {
    guid: directText(template, "GUID") || textOf(document, "ObjectName"),
    componentGuid: directText(template, "ComponentGuid"),
    siteGuid: directText(template, "SiteGuid"),
    transform: {
      position: parseDirectVector(template, "PositionInParent") || ZERO_VECTOR,
      orientation: parseOrientation(firstDirectChild(template, "Orientation"))
    }
  };
}

function parseSiteTemplate(document: Document): SiteTemplate | null {
  const template = firstByLocalName(document, "SiteTemplate");
  if (!template) return null;
  return {
    guid: directText(template, "GUID") || textOf(document, "ObjectName"),
    dimension: parseDirectVector(template, "Dimension"),
    orientation: parseOrientation(firstDirectChild(template, "Orientation")),
    locationGroupName: directText(template, "LocationGroupName")
  };
}

function parseMeshMetadata(document: Document, artifact: SourceArtifact): FluentMeshMetadata | null {
  const meshNode = firstByLocalName(document, "WorktableMesh");
  const meshPayload = textOf(document, "Mesh").replace(/\s+/g, "");
  const guid = normalizeGuid(artifact.meshGuid || stemForPath(artifact.path || artifact.name) || textOf(document, "GUID"));
  const name = artifact.meshName || textOf(document, "ObjectName") || guid || artifact.name;
  const sourcePath = artifact.meshSourcePath || artifact.path || artifact.name;
  const payloadSize = artifact.meshPayloadSize ?? meshPayload.length;
  const decodedSize = artifact.meshDecodedSize ?? decodedBase64Size(meshPayload);
  const modelAssetPath = artifact.modelAssetPath || modelAssetPathForMesh(guid, name);

  if (!meshNode && !meshPayload && !guid) return null;

  return {
    guid,
    name,
    sourcePath,
    payloadSize,
    decodedSize,
    base64Length: meshPayload.length,
    version: meshNode?.getAttribute("version") || "",
    dataVersion: meshNode?.getAttribute("dataVersion") || "",
    checksum: textOf(document, "Checksum"),
    unitScaleToMm: artifact.unitScaleToMm,
    unitScaleSource: artifact.unitScaleSource,
    nativeUnit: artifact.nativeUnit,
    modelAssetPath
  };
}

function parseComponentMeshReferences(document: Document, renderer: string): FluentMeshReference[] {
  const references = allByLocalName(document, "Reference")
    .map((reference) => ({
      guid: normalizeGuid(directText(reference, "Guid") || directText(reference, "GUID")),
      typeId: directText(reference, "TypeId"),
      name: directText(reference, "ObjectName")
    }))
    .filter((reference) => canonicalText(reference.typeId) === "worktablemesh" && isRealGuid(reference.guid));

  if (!references.length) return [];
  const rendererText = canonicalText(renderer);
  const ordered = [...references].sort((a, b) => {
    const aPreferred = rendererText && canonicalText(a.name) === rendererText ? 0 : 1;
    const bPreferred = rendererText && canonicalText(b.name) === rendererText ? 0 : 1;
    return aPreferred - bPreferred;
  });
  const seen = new Set<string>();
  const out: FluentMeshReference[] = [];
  for (const reference of ordered) {
    if (seen.has(reference.guid)) continue;
    seen.add(reference.guid);
    out.push({
      guid: reference.guid,
      name: reference.name || renderer || reference.guid
    });
  }
  return out;
}

function attachMeshesToComponents(components: Map<string, ComponentTemplate>, meshes: Map<string, FluentMeshMetadata>): void {
  components.forEach((component) => {
    if (!component.meshReference) return;
    const mesh = meshes.get(normalizeGuid(component.meshReference.guid)) || meshByName(meshes, component.meshReference.name || component.renderer);
    if (mesh) component.mesh = mesh;
  });
}

function meshByName(meshes: Map<string, FluentMeshMetadata>, name: string): FluentMeshMetadata | null {
  const target = canonicalText(name);
  if (!target) return null;
  return Array.from(meshes.values()).find((mesh) => canonicalText(mesh.name) === target) || null;
}

function modelAssetPathForMeshReference(reference: FluentMeshReference | null): string | undefined {
  if (!reference) return undefined;
  const guid = normalizeGuid(reference.guid);
  if (isRealGuid(guid)) return modelAssetPathForMesh(guid, reference.name);
  return undefined;
}

function modelAssetPathForMesh(guid: string, name: string): string {
  const id = isRealGuid(normalizeGuid(guid)) ? normalizeGuid(guid) : assetStem(name);
  return id ? `/models/fluent/local/${id}.glb` : "";
}

function decodedBase64Size(value: string): number | null {
  if (!value) return null;
  const normalized = value.replace(/\s+/g, "");
  if (!normalized) return null;
  const padding = normalized.endsWith("==") ? 2 : normalized.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((normalized.length * 3) / 4) - padding);
}

function parseWorkspace(
  document: Document,
  artifact: SourceArtifact,
  components: Map<string, ComponentTemplate>,
  connectors: Map<string, ConnectorTemplate>,
  sites: Map<string, SiteTemplate>
): FluentGeometryRecord[] {
  const out: FluentGeometryRecord[] = [];
  const frames = allByLocalName(document, "Frame");
  const source = `FluentControl workspace: ${artifact.name}`;
  frames.forEach((frame) => {
    parseConnectedComponentNode({
      node: frame,
      transform: identityTransform(),
      depth: 0,
      parentLabels: [],
      path: [],
      source,
      siteGuid: "",
      connectorGuid: "",
      components,
      connectors,
      sites,
      out
    });
  });
  return out;
}

function parseConnectedComponentNode(input: {
  node: Element;
  transform: Transform;
  depth: number;
  parentLabels: string[];
  path: string[];
  source: string;
  siteGuid: string;
  connectorGuid: string;
  components: Map<string, ComponentTemplate>;
  connectors: Map<string, ConnectorTemplate>;
  sites: Map<string, SiteTemplate>;
  out: FluentGeometryRecord[];
}): void {
  const templateGuid = directText(input.node, "CarrierOrLabwareTemplateGUID");
  const template = input.components.get(normalizeGuid(templateGuid));
  const label = labwareName(input.node) || template?.name || templateGuid || "Worktable item";
  const adjustment = parseSiteAdjustment(input.node);
  const transform = adjustment ? siteAdjustmentTransform(input.transform, adjustment) : input.transform;
  const path = [...input.path, label].filter(Boolean);
  const role = classifyRecordRole(label, template);
  const catalogName = catalogNameForRecord(label, template, role);

  if (input.depth > 0 && label && templateGuid) {
    input.out.push({
      label,
      catalogName,
      role,
      functionalGroup: template?.functionalGroup || "",
      footprint: template?.footprint || "",
      componentName: template?.name || label,
      location: path.slice(0, -1).join(" / "),
      position: trailingPosition(label),
      meshGuid: template?.mesh?.guid || template?.meshReference?.guid || undefined,
      meshName: template?.mesh?.name || template?.meshReference?.name || template?.renderer || undefined,
      meshSourcePath: template?.mesh?.sourcePath || undefined,
      meshPayloadSize: template?.mesh?.payloadSize,
      meshDecodedSize: template?.mesh?.decodedSize,
      unitScaleToMm: template?.mesh?.unitScaleToMm,
      unitScaleSource: template?.mesh?.unitScaleSource,
      nativeUnit: template?.mesh?.nativeUnit,
      modelAssetPath: template?.mesh?.modelAssetPath || modelAssetPathForMeshReference(template?.meshReference || null),
      wellSites: template?.wellSites || [],
      transform: {
        source: input.source,
        positionMm: transform.position,
        orientation: transform.orientation,
        dimensionMm: template?.dimension || null,
        componentGuid: templateGuid,
        connectorGuid: input.connectorGuid,
        siteGuid: input.siteGuid,
        stackLevel: Math.max(0, input.depth - 1),
        parentLabels: input.parentLabels,
        path,
        unitScaleToMm: template?.mesh?.unitScaleToMm,
        unitScaleSource: template?.mesh?.unitScaleSource,
        nativeUnit: template?.mesh?.nativeUnit
      }
    });
  }

  const arrangementElements = directChildren(firstDirectChild(input.node, "Arrangements"), "Arrangement");
  arrangementElements.forEach((arrangementElement) => {
    const arrangementId = directText(arrangementElement, "TemplateID");
    const templateArrangement =
      template?.arrangements.get(normalizeGuid(arrangementId)) || template?.firstArrangement || null;

    directSiteEntries(firstDirectChild(arrangementElement, "Sites")).forEach((entry) => {
      const siteIndex = Number(directText(entry, "Key"));
      const site = firstDirectChild(entry, "Value");
      if (!site || !Number.isFinite(siteIndex)) return;

      const siteGuid = directText(site, "BaseTemplateGuid") || templateArrangement?.siteTemplateGuids.get(siteIndex) || "";
      const siteTemplate = input.sites.get(normalizeGuid(siteGuid));
      const siteOffset = siteOffsetFor(templateArrangement, siteIndex);
      const siteTransform = transformFromParts(siteOffset, siteTemplate?.orientation || IDENTITY_MATRIX);
      const adjustmentTransform = parseAdjustment(site);
      const connectorGuid = directText(site, "ConnectorTemplateGuid");
      const connector = input.connectors.get(normalizeGuid(connectorGuid));
      const connectorTransform = connector?.transform || identityTransform();
      const childTransform = multiplyTransforms(
        transform,
        multiplyTransforms(siteTransform, multiplyTransforms(adjustmentTransform, connectorTransform))
      );

      const connected = firstDirectChild(site, "ConnectedComponent");
      if (!connected || isNilElement(connected)) return;
      parseConnectedComponentNode({
        ...input,
        node: connected,
        transform: childTransform,
        depth: input.depth + 1,
        parentLabels: input.depth > 0 ? [...input.parentLabels, label] : input.parentLabels,
        path,
        siteGuid,
        connectorGuid,
        out: input.out
      });
    });
  });
}

function classifyRecordRole(label: string, template: ComponentTemplate | undefined): FluentComponentRole {
  if (!template) return classifyComponentRole(label, "", "");
  const labelRole = classifyComponentRole(label, template.functionalGroup, template.footprint);
  if (labelRole !== "unknown") return labelRole;
  return template.role;
}

function classifyComponentRole(name: string, functionalGroup: string, footprint: string): FluentComponentRole {
  const nameText = canonicalText(name);
  const group = canonicalText(functionalGroup);
  const footprintText = canonicalText(footprint);

  // Exact object-name phrases — Tube Runner FG is Carrier.Miscellaneous (ambiguous).
  if (hasAny(nameText, ["tube runner", "tube holder"])) return "tube-rack";

  if (group.includes("diti")) return "tip-box";
  if (group.includes("trough")) return "labware";
  if (group.includes("wash and waste") || (group.includes("wash") && group.includes("waste"))) return "waste";
  if (group.includes("microplate") || group.includes("deep well")) return "labware";
  if (group.startsWith("labware")) return "labware";
  if (group.includes("tool")) return "tool";
  if (group.includes("3rd party") || group.includes("device")) return "device";
  if (group.includes("base unit")) return "structural";
  if (group.includes("deck extension") || group.includes("deck segment")) return "deck";
  if (group.includes("grid segment") || group.includes("hotel") || group.startsWith("carrier")) return "carrier";
  if (group.includes("nest")) return "nest";

  // Footprint / FG empty: fail closed — no falcon/plate/tip keyword invent.
  if (footprintText.includes("sbs")) return "labware";
  return "unknown";
}

function catalogNameForRecord(label: string, template: ComponentTemplate | undefined, _role: FluentComponentRole): string {
  // Exact FluentControl component/template name only — never invent CapHolder / Falcon / stock catalog.
  return (template?.name || label || "").trim() || label;
}

function siteOffsetFor(arrangement: ArrangementTemplate | null, siteIndex: number): Vector3Model {
  if (!arrangement) return ZERO_VECTOR;
  const x = siteIndex % Math.max(1, arrangement.sitesInX);
  const y = Math.floor(siteIndex / Math.max(1, arrangement.sitesInX)) % Math.max(1, arrangement.sitesInY);
  const z = Math.floor(siteIndex / Math.max(1, arrangement.sitesInX * arrangement.sitesInY));
  const gridOffset = {
    x: x * arrangement.spacing.x,
    y: y * arrangement.spacing.y,
    z: z * arrangement.spacing.z
  };
  return addVectors(addVectors(arrangement.positionInParent, gridOffset), arrangement.siteOffsets.get(siteIndex) || ZERO_VECTOR);
}

function parseTemplateWellSites(template: Element, arrangements: Map<string, ArrangementTemplate>): FluentWellSiteModel[] {
  const pipettableSites = parsePipettableWellSites(template);
  if (pipettableSites.length) return pipettableSites;

  const arrangement = Array.from(arrangements.values())
    .filter((candidate) => arrangementSiteCount(candidate) > 1 && arrangementSiteCount(candidate) <= MAX_WELL_SITE_COUNT)
    .sort((a, b) => arrangementSiteCount(b) - arrangementSiteCount(a))[0];
  return arrangement ? arrangementWellSites(arrangement) : [];
}

function parsePipettableWellSites(template: Element): FluentWellSiteModel[] {
  const pipettable = firstDirectChild(template, "Pipettable");
  if (!pipettable || isNilElement(pipettable)) return [];

  const firstWell = parseDirectVector(pipettable, "PositionOfFirstWell");
  const cols = positiveInteger(directNumberText(pipettable, "XNumberOfWells"));
  const rows = positiveInteger(directNumberText(pipettable, "YNumberOfWells"));
  if (!firstWell || !rows || !cols || rows * cols > MAX_WELL_SITE_COUNT) return [];

  const xSpacing = directNumberText(pipettable, "XSpacing") ?? 0;
  const ySpacing = directNumberText(pipettable, "YSpacing") ?? 0;
  const cavity = firstByLocalName(pipettable, "Cavity");
  const wellGeometry = parsePipettableCavityGeometry(pipettable);
  const offset = cavity ? parseDirectVector(cavity, "OffsetToMain") || ZERO_VECTOR : ZERO_VECTOR;

  const sites: FluentWellSiteModel[] = [];
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const index = row * cols + col;
      sites.push({
        index,
        row,
        col,
        source: "fluent-pipettable",
        positionMm: addVectors(addVectors(firstWell, offset), {
          x: col * xSpacing,
          y: row * ySpacing,
          z: 0
        }),
        wellShape: wellGeometry.wellShape,
        diameterMm: wellGeometry.wellDiameterMm,
        depthMm: wellGeometry.wellDepthMm
      });
    }
  }
  return sites;
}

function parsePipettableCavityGeometry(pipettable: Element): FluentWellGeometry {
  const cavity = firstByLocalName(pipettable, "Cavity");
  const cavityDefinition = cavity ? firstByLocalName(cavity, "CavityDefinition") : null;
  const shapeList = cavityDefinition ? firstByLocalName(cavityDefinition, "ShapeList") : null;
  const shapeParent = shapeList || cavityDefinition || cavity;
  const shapes = directChildren(shapeParent).filter((shape) => normalizeName(localName(shape)).includes("cavityshape"));
  if (!shapes.length) return {};

  const wellDepthMm = shapes.reduce((sum, shape) => sum + (directNumberText(shape, "Height") || 0), 0);
  const topShape = shapes[shapes.length - 1];
  const fallbackDiameter = firstFiniteNumber(shapes.map(cavityShapeDiameterMm));
  return {
    wellShape: cavityShapeFootprint(topShape),
    wellDiameterMm: cavityShapeDiameterMm(topShape) || fallbackDiameter,
    wellDepthMm: wellDepthMm > 0 ? wellDepthMm : undefined
  };
}

function cavityShapeFootprint(shape: Element): WellShape | undefined {
  const shapeType = `${shape.getAttribute("i:type") || shape.getAttribute("type") || shape.getAttribute("xsi:type") || localName(shape)}`.toLowerCase();
  if (shapeType.includes("cylinder") || shapeType.includes("cone")) return "round";
  if (shapeType.includes("cuboid") || shapeType.includes("pyramid") || shapeType.includes("trapezoid")) return "square";
  if (directNumberText(shape, "Diameter") || directNumberText(shape, "DiameterTop") || directNumberText(shape, "DiameterBottom")) return "round";
  if (directNumberText(shape, "Length") || directNumberText(shape, "Width") || directNumberText(shape, "AreaTop")) return "square";
  return undefined;
}

function cavityShapeDiameterMm(shape: Element): number | undefined {
  const diameter =
    directNumberText(shape, "DiameterTop") ||
    directNumberText(shape, "Diameter") ||
    directNumberText(shape, "DiameterBottom") ||
    maxFiniteNumber([directNumberText(shape, "Length"), directNumberText(shape, "Width")]);
  if (diameter && diameter > 0) return diameter;

  const area = directNumberText(shape, "AreaTop") || directNumberText(shape, "AreaBottom");
  return area && area > 0 ? Math.sqrt(area) : undefined;
}

function firstFiniteNumber(values: Array<number | undefined | null>): number | undefined {
  return values.find((value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0);
}

function maxFiniteNumber(values: Array<number | undefined | null>): number | undefined {
  const finiteValues = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0);
  return finiteValues.length ? Math.max(...finiteValues) : undefined;
}

function fluentWellGeometryFromSites(wellSites: FluentWellSiteModel[]): FluentWellGeometry {
  const first = wellSites.find((site) => site.wellShape || site.diameterMm || site.depthMm);
  return {
    wellShape: first?.wellShape,
    wellDiameterMm: first?.diameterMm,
    wellDepthMm: first?.depthMm
  };
}

function arrangementWellSites(arrangement: ArrangementTemplate): FluentWellSiteModel[] {
  const count = arrangementSiteCount(arrangement);
  if (count <= 1 || count > MAX_WELL_SITE_COUNT) return [];

  return Array.from({ length: count }, (_, index) => {
    const x = index % Math.max(1, arrangement.sitesInX);
    const y = Math.floor(index / Math.max(1, arrangement.sitesInX)) % Math.max(1, arrangement.sitesInY);
    return {
      index,
      row: y,
      col: x,
      source: "fluent-arrangement",
      siteGuid: arrangement.siteTemplateGuids.get(index),
      positionMm: siteOffsetFor(arrangement, index)
    };
  });
}

function arrangementSiteCount(arrangement: ArrangementTemplate): number {
  return Math.max(1, arrangement.sitesInX) * Math.max(1, arrangement.sitesInY) * Math.max(1, arrangement.sitesInZ);
}

function parseAdjustment(site: Element): Transform {
  const adjustment = firstDirectChild(site, "Adjustment");
  if (!adjustment || isNilElement(adjustment)) return identityTransform();
  const origin = parseDirectVector(adjustment, "origin") || parseDirectVector(adjustment, "Origin") || ZERO_VECTOR;
  const orientation = parseOrientation(firstDirectChild(adjustment, "orientation") || firstDirectChild(adjustment, "Orientation"));
  return transformFromParts(origin, orientation);
}

function parseSiteAdjustment(component: Element): { x?: number; y?: number; z?: number; phi?: number } | null {
  const custom = firstDirectChild(component, "CustomAttribute");
  if (!custom || isNilElement(custom)) return null;
  const values: { x?: number; y?: number; z?: number; phi?: number } = {};
  directChildren(custom).forEach((entry) => {
    const key = directText(entry, "Key").toLowerCase();
    const value = numberFromStringContent(entry);
    if (value === null) return;
    if (key.endsWith(":x")) values.x = value;
    if (key.endsWith(":y")) values.y = value;
    if (key.endsWith(":z")) values.z = value;
    if (key.endsWith(":phi")) values.phi = value;
  });
  return Object.keys(values).length ? values : null;
}

function siteAdjustmentTransform(parent: Transform, adjustment: { x?: number; y?: number; z?: number; phi?: number }): Transform {
  const local = transformFromParts(
    {
      x: adjustment.x || 0,
      y: adjustment.y || 0,
      z: adjustment.z || 0
    },
    rotationAroundZ(degreesToRadians(adjustment.phi || 0))
  );
  const isAbsolute = Math.abs(adjustment.x || 0) > 250 || Math.abs(adjustment.y || 0) > 250;
  return isAbsolute ? local : multiplyTransforms(parent, local);
}

function labwareName(component: Element): string {
  const labwareNameNode = firstDirectChild(component, "LabwareName");
  if (!labwareNameNode || isNilElement(labwareNameNode)) return "";
  return firstString(allByLocalName(labwareNameNode, "Value").map((node) => node.textContent?.trim() || ""));
}

function trailingPosition(label: string): number | null {
  const match = label.match(/\[(\d+)\]\s*$/);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function parseVectorMap(container: Element | null): Map<number, Vector3Model> {
  const out = new Map<number, Vector3Model>();
  if (!container) return out;
  directChildren(container).forEach((entry) => {
    const key = Number(directText(entry, "Key"));
    const value = firstDirectChild(entry, "Value");
    const vector = value ? parseVector(value) : null;
    if (Number.isFinite(key) && vector) out.set(key, vector);
  });
  return out;
}

function parseGuidMap(container: Element | null): Map<number, string> {
  const out = new Map<number, string>();
  if (!container) return out;
  directChildren(container).forEach((entry) => {
    const key = Number(directText(entry, "Key"));
    const value = directText(entry, "Value");
    if (Number.isFinite(key) && value) out.set(key, value);
  });
  return out;
}

function parseDirectVector(parent: Element, childName: string): Vector3Model | null {
  const child = firstDirectChild(parent, childName);
  return child ? parseVector(child) : null;
}

function parseVector(element: Element): Vector3Model | null {
  const origin = firstDirectChild(element, "origin") || firstDirectChild(element, "Origin");
  const source = origin || element;
  const x = numberText(source, "X");
  const y = numberText(source, "Y");
  const z = numberText(source, "Z");
  if (x === null || y === null || z === null) return null;
  return { x, y, z };
}

function parseOrientation(element: Element | null): Matrix3Model {
  if (!element || isNilElement(element)) return IDENTITY_MATRIX;
  const mat = firstByLocalName(element, "Mat");
  if (!mat) return IDENTITY_MATRIX;
  const rows = directChildren(mat)
    .map((row) => directChildren(row, "double").map((value) => Number(value.textContent?.trim())))
    .filter((row) => row.length >= 3 && row.slice(0, 3).every(Number.isFinite))
    .map((row) => row.slice(0, 3));
  if (rows.length < 3) return IDENTITY_MATRIX;
  return [
    [rows[0][0], rows[0][1], rows[0][2]],
    [rows[1][0], rows[1][1], rows[1][2]],
    [rows[2][0], rows[2][1], rows[2][2]]
  ];
}

function numberFromStringContent(element: Element): number | null {
  const text = textOf(element, "StringContent");
  const match = text.match(/>([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)</) || text.match(/[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?/);
  const value = Number(match?.[1] || match?.[0] || text);
  return Number.isFinite(value) ? value : null;
}

function transformFromParts(position: Vector3Model, orientation: Matrix3Model): Transform {
  return { position, orientation };
}

function identityTransform(): Transform {
  return transformFromParts(ZERO_VECTOR, IDENTITY_MATRIX);
}

function multiplyTransforms(parent: Transform, child: Transform): Transform {
  return {
    orientation: multiplyMatrices(parent.orientation, child.orientation),
    position: addVectors(parent.position, multiplyMatrixVector(parent.orientation, child.position))
  };
}

function multiplyMatrices(a: Matrix3Model, b: Matrix3Model): Matrix3Model {
  return [
    [
      a[0][0] * b[0][0] + a[0][1] * b[1][0] + a[0][2] * b[2][0],
      a[0][0] * b[0][1] + a[0][1] * b[1][1] + a[0][2] * b[2][1],
      a[0][0] * b[0][2] + a[0][1] * b[1][2] + a[0][2] * b[2][2]
    ],
    [
      a[1][0] * b[0][0] + a[1][1] * b[1][0] + a[1][2] * b[2][0],
      a[1][0] * b[0][1] + a[1][1] * b[1][1] + a[1][2] * b[2][1],
      a[1][0] * b[0][2] + a[1][1] * b[1][2] + a[1][2] * b[2][2]
    ],
    [
      a[2][0] * b[0][0] + a[2][1] * b[1][0] + a[2][2] * b[2][0],
      a[2][0] * b[0][1] + a[2][1] * b[1][1] + a[2][2] * b[2][1],
      a[2][0] * b[0][2] + a[2][1] * b[1][2] + a[2][2] * b[2][2]
    ]
  ];
}

function multiplyMatrixVector(matrix: Matrix3Model, vector: Vector3Model): Vector3Model {
  return {
    x: matrix[0][0] * vector.x + matrix[0][1] * vector.y + matrix[0][2] * vector.z,
    y: matrix[1][0] * vector.x + matrix[1][1] * vector.y + matrix[1][2] * vector.z,
    z: matrix[2][0] * vector.x + matrix[2][1] * vector.y + matrix[2][2] * vector.z
  };
}

function addVectors(a: Vector3Model, b: Vector3Model): Vector3Model {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

function rotationAroundZ(radians: number): Matrix3Model {
  const c = Math.cos(radians);
  const s = Math.sin(radians);
  return [
    [c, -s, 0],
    [s, c, 0],
    [0, 0, 1]
  ];
}

function degreesToRadians(value: number): number {
  return (value * Math.PI) / 180;
}

function dedupeGeometryRecords(records: FluentGeometryRecord[]): FluentGeometryRecord[] {
  const byKey = new Map<string, FluentGeometryRecord>();
  records.forEach((record) => {
    const key = `${record.label.toLowerCase()}:${record.transform.componentGuid}:${record.transform.positionMm.x}:${record.transform.positionMm.y}:${record.transform.positionMm.z}`;
    if (!byKey.has(key)) byKey.set(key, record);
  });
  return Array.from(byKey.values()).sort((a, b) => {
    const ap = a.transform.positionMm;
    const bp = b.transform.positionMm;
    return ap.x - bp.x || ap.y - bp.y || ap.z - bp.z || a.label.localeCompare(b.label);
  });
}

function countRecordsByRole(records: FluentGeometryRecord[]): Map<FluentComponentRole, number> {
  const counts = new Map<FluentComponentRole, number>();
  records.forEach((record) => counts.set(record.role, (counts.get(record.role) || 0) + 1));
  return counts;
}

function textOf(root: ParentNode, name: string): string {
  const element = firstByLocalName(root, name);
  return element?.textContent?.trim() || "";
}

function numberText(root: ParentNode, name: string): number | null {
  const text = textOf(root, name);
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

function directNumberText(parent: Element | null, name: string): number | null {
  const text = directText(parent, name);
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

function directText(parent: Element | null, name: string): string {
  const child = firstDirectChild(parent, name);
  return child?.textContent?.trim() || "";
}

function firstByLocalName(root: ParentNode, name: string): Element | null {
  return allByLocalName(root, name)[0] || null;
}

function allByLocalName(root: ParentNode, name: string): Element[] {
  const wanted = normalizeName(name);
  const elements = isElementNode(root)
    ? [root, ...Array.from(root.getElementsByTagName("*"))]
    : Array.from((root as Document).getElementsByTagName("*"));
  return elements.filter((element) => normalizeName(localName(element)) === wanted);
}

function isElementNode(node: ParentNode): node is Element {
  return typeof (node as { nodeType?: unknown }).nodeType === "number" && (node as { nodeType: number }).nodeType === 1;
}

function firstDirectChild(parent: Element | null, name: string): Element | null {
  return directChildren(parent, name)[0] || null;
}

function directSiteEntries(parent: Element | null): Element[] {
  if (!parent) return [];
  return childElements(parent).filter((child) => normalizeName(localName(child)).startsWith("keyvalueofintsite"));
}

function directChildren(parent: Element | null, name?: string): Element[] {
  if (!parent) return [];
  const wanted = name ? normalizeName(name) : "";
  return childElements(parent).filter((child) => !wanted || normalizeName(localName(child)) === wanted);
}

function childElements(parent: Element): Element[] {
  if ("children" in parent && parent.children) {
    return Array.from(parent.children) as Element[];
  }
  return Array.from(parent.childNodes || []).filter(
    (child): child is Element =>
      typeof (child as { nodeType?: unknown }).nodeType === "number"
      && (child as { nodeType: number }).nodeType === 1,
  );
}

function localName(element: Element): string {
  return element.localName || element.tagName.split(":").pop() || element.tagName;
}

function normalizeName(name: string): string {
  return name.toLowerCase();
}

function normalizeGuid(value: string): string {
  const match = (value || "").match(GUID_IN_TEXT_PATTERN);
  return match?.[0].toLowerCase() || "";
}

function isRealGuid(value: string): boolean {
  const normalized = normalizeGuid(value);
  return Boolean(normalized && normalized !== EMPTY_GUID);
}

function positiveInteger(value: number | null): number {
  const integer = Math.floor(Number(value));
  return Number.isFinite(integer) && integer > 0 ? integer : 0;
}

function stemForPath(pathOrName: string): string {
  const name = pathOrName.split(/[\\/]/).filter(Boolean).pop() || pathOrName;
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(0, index) : name;
}

function assetStem(value: string): string {
  return canonicalText(value).replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

function isNilElement(element: Element): boolean {
  return Array.from(element.attributes).some((attribute) => attribute.name.endsWith(":nil") && attribute.value.toLowerCase() === "true");
}

function firstString(values: Array<string | undefined>): string {
  return values.find((value) => Boolean(value)) || "";
}

function hasAny(text: string, needles: string[]): boolean {
  return needles.some((needle) => text.includes(needle));
}

function canonicalText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/[,/]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function stripByteOrderMark(value: string): string {
  return value.charCodeAt(0) === 0xfeff ? value.slice(1) : value;
}
