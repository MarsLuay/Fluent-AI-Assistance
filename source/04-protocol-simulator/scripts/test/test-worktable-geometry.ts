import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DOMParser } from "@xmldom/xmldom";
import type { Matrix3Model, SourceArtifact, Vector3Model } from "../../src/types";
import { parseFluentGeometryArtifacts } from "../../src/data/fluentGeometry";
import type { FluentGeometryRecord, FluentWellSiteModel } from "../../src/data/fluentGeometry";
import {
  listLabwareGeometryCatalog,
  registerLabwareCatalogFromDefinitions,
  registerLabwareCatalogPayload,
  resolveLabwareGeometry,
  resolveModelAssetPath
} from "../../src/data/labwareCatalog";

globalThis.DOMParser = DOMParser as unknown as typeof globalThis.DOMParser;

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const PROTOCOL_BUILDER_ROOT = path.join(PROJECT_ROOT, "source/03-protocol-builder");
const MODEL_MANIFEST_PATH = path.join(
  PROJECT_ROOT,
  "source/04-protocol-simulator/public/models/fluent/local/manifest.json"
);
const WORKTABLE_EXTENSIONS = new Set([".xwsp", ".xcmp", ".xcon", ".xsit"]);
const SKIPPED_DIRECTORIES = new Set([".git", "node_modules", "__pycache__", "vendor"]);
const WORKTABLE_FILE_CACHE = new Map<string, Map<string, string[]>>();
const DISCOVERY_PROGRESS_INTERVAL_MS = 5000;
const INCLUDE_LOCAL_WORKTABLE_FIXTURES = process.env.TECAN_INCLUDE_LOCAL_WORKTABLE_FIXTURES === "1";
const POSITION_TOLERANCE_MM = 0.001;
const ORIENTATION_TOLERANCE = 0.001;
const SITE_BOUND_TOLERANCE_MM = 25;

type WorktableFixture = {
  root: string;
  label: string;
  files: string[];
  workspaceFile: string;
};

console.log(`discovering FluentControl worktable fixtures under ${path.relative(PROJECT_ROOT, PROTOCOL_BUILDER_ROOT).replace(/\\/g, "/")}`);
const fixtures = discoverWorktableFixtures(PROTOCOL_BUILDER_ROOT);
if (!fixtures.length) throw new Error("No FluentControl worktable fixtures were discovered.");
console.log(`discovered ${fixtures.length} workspace fixture(s)`);

let workspaceCount = 0;
let labwareCount = 0;

fixtures.forEach((fixture) => {
  console.log(`checking ${fixture.label}`);
  const artifacts = fixture.files.map((filePath, index) => sourceArtifactFor(filePath, fixture.root, index));
  const parsed = parseFluentGeometryArtifacts(artifacts);
  const hasConnectedComponents = workspaceHasConnectedComponent(fixture.workspaceFile);
  const unreadableWarnings = parsed.warnings.filter((warning) => warning.includes("Skipped unreadable Fluent worktable XML"));

  if (unreadableWarnings.length) {
    throw new Error(`${fixture.label} emitted unreadable XML warnings: ${unreadableWarnings.join("; ")}`);
  }
  if (!(parsed.metadata.worktableName || parsed.metadata.name)) {
    throw new Error(`${fixture.label} did not expose a workspace name.`);
  }
  if (hasConnectedComponents && parsed.labware.length === 0) {
    throw new Error(`${fixture.label} has connected components but produced no simulator labware.`);
  }
  parsed.labware.forEach((record) => {
    const position = record.transform.positionMm;
    if (![position.x, position.y, position.z].every(Number.isFinite)) {
      throw new Error(`${fixture.label} produced non-finite position for ${record.label}.`);
    }
  });
  validateRenderedFidelity(fixture.label, parsed.labware);

  workspaceCount += 1;
  labwareCount += parsed.labware.length;
  console.log(`ok ${fixture.label}: ${parsed.labware.length} component(s)`);
});

const modelCount = validateModelManifest();
console.log(`ok model manifest: ${modelCount} converted model metadata record(s)`);
validateDirectCavityShapeParsing();
validateImportedLabwareDimsAndMeshes();
console.log(`validated ${fixtures.length} workspace fixture(s), ${workspaceCount} workspace file(s), ${labwareCount} parsed component(s)`);

function discoverWorktableFixtures(root: string): WorktableFixture[] {
  return discoverWorktableRoots(root)
    .flatMap((worktableRoot) => {
      const workspaceFiles = collectWorkspaceFiles(worktableRoot);
      return workspaceFiles.map((workspaceFile) => ({
        root: worktableRoot,
        label: path.relative(PROJECT_ROOT, workspaceFile).replace(/\\/g, "/"),
        files: collectWorktableFiles(worktableRoot, workspaceFile),
        workspaceFile
      }));
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

function discoverWorktableRoots(root: string): string[] {
  const roots = new Set<string>();

  function addIfWorktableRoot(candidate: string): void {
    if (fs.existsSync(path.join(candidate, "Workspaces"))) roots.add(candidate);
  }

  addIfWorktableRoot(path.join(root, "libs/fluentcoder/tests/fixtures/synthetic_catalog/install/SystemSpecific/Worktable"));

  // Imported projects and generation outputs are local, untracked corpora that
  // can turn this deterministic CI test into an unbounded filesystem walk.
  // Exercise those explicitly when diagnosing a local worktable import.
  if (!INCLUDE_LOCAL_WORKTABLE_FIXTURES) return Array.from(roots);

  const projectsRoot = path.join(root, "projects");
  if (fs.existsSync(projectsRoot)) {
    fs.readdirSync(projectsRoot, { withFileTypes: true }).forEach((entry) => {
      if (!entry.isDirectory() || SKIPPED_DIRECTORIES.has(entry.name)) return;
      // Full-export corpora are huge; keep analyzer/CI worktable checks on sample fixtures.
      if (/full[_-]?export|corpus|command_corpus/i.test(entry.name)) return;
      addIfWorktableRoot(path.join(projectsRoot, entry.name, "extracted/DataStore/SystemSpecific/Worktable"));
    });
  }

  const buildRoot = path.join(root, "build");
  if (fs.existsSync(buildRoot)) {
    fs.readdirSync(buildRoot, { withFileTypes: true }).forEach((entry) => {
      if (!entry.isDirectory() || SKIPPED_DIRECTORIES.has(entry.name)) return;
      const firstLevel = path.join(buildRoot, entry.name);
      addIfWorktableRoot(path.join(firstLevel, "DataStore/SystemSpecific/Worktable"));
      safeReadDirectory(firstLevel).forEach((child) => {
        if (child.isDirectory() && !SKIPPED_DIRECTORIES.has(child.name)) {
          addIfWorktableRoot(path.join(firstLevel, child.name, "DataStore/SystemSpecific/Worktable"));
        }
      });
    });
  }

  return Array.from(roots);
}

function collectWorkspaceFiles(root: string): string[] {
  const workspaceRoot = path.join(root, "Workspaces");
  if (!fs.existsSync(workspaceRoot)) return [];
  return fs
    .readdirSync(workspaceRoot, { withFileTypes: true })
    .filter((entry) => entry.isFile() && path.extname(entry.name).toLowerCase() === ".xwsp")
    .map((entry) => path.join(workspaceRoot, entry.name))
    .sort((a, b) => a.localeCompare(b));
}

function collectWorktableFiles(root: string, workspaceFile: string): string[] {
  const filesByExtension = worktableFilesByExtension(root);
  const referencedGuids = guidsReferencedBy([workspaceFile]);
  const selectedFiles = [workspaceFile];

  [".xcmp", ".xsit", ".xcon"].forEach((extension) => {
    const matching = (filesByExtension.get(extension) || []).filter((filePath) => referencedGuids.has(path.basename(filePath, extension).toLowerCase()));
    selectedFiles.push(...matching);
    matching.forEach((filePath) => addGuidsFromText(fs.readFileSync(filePath, "utf8"), referencedGuids));
  });

  return Array.from(new Set(selectedFiles)).sort((a, b) => a.localeCompare(b));
}

function worktableFilesByExtension(root: string): Map<string, string[]> {
  const cached = WORKTABLE_FILE_CACHE.get(root);
  if (cached) return cached;
  const filesByExtension = new Map<string, string[]>();
  let visitedDirectories = 0;
  let lastProgressAt = Date.now();

  function visit(directory: string): void {
    if (!fs.existsSync(directory)) return;
    visitedDirectories += 1;
    if (Date.now() - lastProgressAt > DISCOVERY_PROGRESS_INTERVAL_MS) {
      console.log(`discovering ${path.relative(PROJECT_ROOT, directory).replace(/\\/g, "/")} (${visitedDirectories} directories scanned)`);
      lastProgressAt = Date.now();
    }
    safeReadDirectory(directory).forEach((entry) => {
      const absolutePath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (SKIPPED_DIRECTORIES.has(entry.name)) return;
        visit(absolutePath);
        return;
      }
      const extension = path.extname(entry.name).toLowerCase();
      if (entry.isFile() && WORKTABLE_EXTENSIONS.has(extension)) {
        filesByExtension.set(extension, [...(filesByExtension.get(extension) || []), absolutePath]);
      }
    });
  }

  visit(root);
  WORKTABLE_FILE_CACHE.set(root, filesByExtension);
  return filesByExtension;
}

function safeReadDirectory(directory: string): fs.Dirent[] {
  try {
    return fs.readdirSync(directory, { withFileTypes: true });
  } catch (error) {
    console.warn(`warning: skipped unreadable directory ${path.relative(PROJECT_ROOT, directory).replace(/\\/g, "/")}: ${String(error)}`);
    return [];
  }
}

function guidsReferencedBy(files: string[]): Set<string> {
  const out = new Set<string>();
  files.forEach((filePath) => {
    addGuidsFromText(fs.readFileSync(filePath, "utf8"), out);
  });
  return out;
}

function addGuidsFromText(text: string, out: Set<string>): void {
  for (const match of text.matchAll(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi)) {
    out.add(match[0].toLowerCase());
  }
}

function sourceArtifactFor(filePath: string, fixtureRoot: string, index: number): SourceArtifact {
  const relativePath = path.relative(fixtureRoot, filePath).replace(/\\/g, "/");
  return {
    id: `fixture-${index}`,
    name: path.basename(filePath),
    kind: "worktable-geometry",
    source: path.relative(PROJECT_ROOT, fixtureRoot).replace(/\\/g, "/"),
    text: fs.readFileSync(filePath, "utf8"),
    size: fs.statSync(filePath).size,
    path: relativePath
  };
}

function workspaceHasConnectedComponent(filePath: string): boolean {
  const text = fs.readFileSync(filePath, "utf8");
  return text
    .split(/\r?\n/)
    .some((line) => line.includes("ConnectedComponent") && !line.includes('nil="true"') && !line.includes("/>"));
}

function validateRenderedFidelity(fixtureLabel: string, records: FluentGeometryRecord[]): void {
  const seenLabels = new Set<string>();
  records.forEach((record) => {
    const context = `${fixtureLabel} / ${record.label}`;
    validateUniqueRenderableKey(context, record, seenLabels);
    validateOfficialTransform(context, record);
    validateRenderedBounds(context, record);
    validateOfficialSiteOffsets(context, record);
  });
}

function validateUniqueRenderableKey(context: string, record: FluentGeometryRecord, seenLabels: Set<string>): void {
  const key = `${record.label.toLowerCase()}::${record.transform.componentGuid.toLowerCase()}::${vectorKey(record.transform.positionMm)}`;
  if (seenLabels.has(key)) throw new Error(`${context} produced duplicate renderable component key ${key}.`);
  seenLabels.add(key);
}

function validateOfficialTransform(context: string, record: FluentGeometryRecord): void {
  const transform = record.transform;
  if (!isFiniteVector(transform.positionMm)) throw new Error(`${context} has non-finite official position.`);
  if (!isOrthonormalOrientation(transform.orientation)) {
    throw new Error(`${context} has a non-orthonormal official orientation matrix.`);
  }
  if (transform.dimensionMm) {
    const dimension = transform.dimensionMm;
    if (!isFiniteVector(dimension) || dimension.x < 0 || dimension.y < 0 || dimension.z < 0) {
      throw new Error(`${context} has invalid official dimensions ${JSON.stringify(dimension)}.`);
    }
  }
}

function validateRenderedBounds(context: string, record: FluentGeometryRecord): void {
  const bounds = officialFootprintBounds(record);
  const sizeX = bounds.max.x - bounds.min.x;
  const sizeY = bounds.max.y - bounds.min.y;
  if (![bounds.min.x, bounds.min.y, bounds.max.x, bounds.max.y, sizeX, sizeY].every(Number.isFinite)) {
    throw new Error(`${context} produced non-finite rendered footprint bounds.`);
  }

  const dimension = officialDimension(record);
  if ((dimension.x > POSITION_TOLERANCE_MM && sizeX <= POSITION_TOLERANCE_MM) || (dimension.y > POSITION_TOLERANCE_MM && sizeY <= POSITION_TOLERANCE_MM)) {
    throw new Error(`${context} collapsed official footprint bounds for dimension ${JSON.stringify(dimension)}.`);
  }
}

function validateOfficialSiteOffsets(context: string, record: FluentGeometryRecord): void {
  const sites = record.wellSites || [];
  sites.forEach((site, index) => {
    if (!isFiniteVector(site.positionMm)) throw new Error(`${context} site ${index} has non-finite local offset.`);
    if (site.siteDimensionMm && (!isFiniteVector(site.siteDimensionMm) || site.siteDimensionMm.x < 0 || site.siteDimensionMm.y < 0 || site.siteDimensionMm.z < 0)) {
      throw new Error(`${context} site ${index} has invalid official site dimensions.`);
    }
    site.compatibleComponentGuids?.forEach((guid) => {
      if (!/^[0-9a-f-]{36}$/i.test(guid)) throw new Error(`${context} site ${index} has invalid compatible component GUID ${guid}.`);
    });

    const world = transformLocalPoint(record.transform.positionMm, record.transform.orientation, site.positionMm);
    if (!isFiniteVector(world)) throw new Error(`${context} site ${index} produced non-finite rendered world position.`);
    if (site.source === "fluent-pipettable" && !siteWithinOfficialFootprint(record, site, world)) {
      throw new Error(`${context} site ${index} renders outside official component bounds by more than ${SITE_BOUND_TOLERANCE_MM} mm.`);
    }
  });
  validateArrangementSiteSpacing(context, sites);
}

function validateArrangementSiteSpacing(context: string, sites: FluentWellSiteModel[]): void {
  const arrangementSites = sites.filter((site) => site.source === "fluent-arrangement");
  if (arrangementSites.length < 2) return;
  const uniqueOffsets = new Set(arrangementSites.map((site) => vectorKey(site.positionMm)));
  if (uniqueOffsets.size !== arrangementSites.length) {
    throw new Error(`${context} has duplicate official arrangement site offsets.`);
  }
  const nearestSpacing = nearestPositiveDistance(arrangementSites.map((site) => site.positionMm));
  if (nearestSpacing !== null && nearestSpacing <= POSITION_TOLERANCE_MM) {
    throw new Error(`${context} has collapsed official arrangement site spacing.`);
  }
}

function nearestPositiveDistance(points: Vector3Model[]): number | null {
  let best = Number.POSITIVE_INFINITY;
  points.forEach((point, index) => {
    for (let otherIndex = index + 1; otherIndex < points.length; otherIndex += 1) {
      const distance = vectorLength({ x: point.x - points[otherIndex].x, y: point.y - points[otherIndex].y, z: point.z - points[otherIndex].z });
      if (distance > 0 && distance < best) best = distance;
    }
  });
  return Number.isFinite(best) ? best : null;
}

function siteWithinOfficialFootprint(record: FluentGeometryRecord, site: FluentWellSiteModel, world: Vector3Model): boolean {
  const dimension = officialDimension(record);
  const local = site.positionMm;
  const maxX = Math.max(dimension.x, site.siteDimensionMm?.x || 0) + SITE_BOUND_TOLERANCE_MM;
  const maxY = Math.max(dimension.y, site.siteDimensionMm?.y || 0) + SITE_BOUND_TOLERANCE_MM;
  const inLocalBounds = local.x >= -SITE_BOUND_TOLERANCE_MM && local.x <= maxX && local.y >= -SITE_BOUND_TOLERANCE_MM && local.y <= maxY;
  if (inLocalBounds) return true;

  const bounds = officialFootprintBounds(record);
  return (
    world.x >= bounds.min.x - SITE_BOUND_TOLERANCE_MM &&
    world.x <= bounds.max.x + SITE_BOUND_TOLERANCE_MM &&
    world.y >= bounds.min.y - SITE_BOUND_TOLERANCE_MM &&
    world.y <= bounds.max.y + SITE_BOUND_TOLERANCE_MM
  );
}

function officialFootprintBounds(record: FluentGeometryRecord): { min: { x: number; y: number }; max: { x: number; y: number } } {
  const dimension = officialDimension(record);
  const origin = record.transform.positionMm;
  const orientation = record.transform.orientation;
  const corners = [
    { x: 0, y: 0, z: 0 },
    { x: dimension.x, y: 0, z: 0 },
    { x: dimension.x, y: dimension.y, z: 0 },
    { x: 0, y: dimension.y, z: 0 }
  ].map((point) => transformLocalPoint(origin, orientation, point));
  return {
    min: { x: Math.min(...corners.map((point) => point.x)), y: Math.min(...corners.map((point) => point.y)) },
    max: { x: Math.max(...corners.map((point) => point.x)), y: Math.max(...corners.map((point) => point.y)) }
  };
}

function officialDimension(record: FluentGeometryRecord): Vector3Model {
  return record.transform.dimensionMm || { x: 0, y: 0, z: 0 };
}

function transformLocalPoint(origin: Vector3Model, orientation: Matrix3Model, point: Vector3Model): Vector3Model {
  const rotated = rotateVector(orientation, point);
  return { x: origin.x + rotated.x, y: origin.y + rotated.y, z: origin.z + rotated.z };
}

function rotateVector(matrix: Matrix3Model, vector: Vector3Model): Vector3Model {
  return {
    x: matrix[0][0] * vector.x + matrix[0][1] * vector.y + matrix[0][2] * vector.z,
    y: matrix[1][0] * vector.x + matrix[1][1] * vector.y + matrix[1][2] * vector.z,
    z: matrix[2][0] * vector.x + matrix[2][1] * vector.y + matrix[2][2] * vector.z
  };
}

function isOrthonormalOrientation(matrix: Matrix3Model): boolean {
  const rows = matrix.map((row) => ({ x: row[0], y: row[1], z: row[2] }));
  if (!rows.every(isFiniteVector)) return false;
  return (
    rows.every((row) => Math.abs(vectorLength(row) - 1) <= ORIENTATION_TOLERANCE) &&
    Math.abs(dotProduct(rows[0], rows[1])) <= ORIENTATION_TOLERANCE &&
    Math.abs(dotProduct(rows[0], rows[2])) <= ORIENTATION_TOLERANCE &&
    Math.abs(dotProduct(rows[1], rows[2])) <= ORIENTATION_TOLERANCE
  );
}

function vectorLength(vector: Vector3Model): number {
  return Math.sqrt(dotProduct(vector, vector));
}

function dotProduct(a: Vector3Model, b: Vector3Model): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

function isFiniteVector(vector: Vector3Model): boolean {
  return Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z);
}

function vectorKey(vector: Vector3Model): string {
  return `${vector.x.toFixed(3)}:${vector.y.toFixed(3)}:${vector.z.toFixed(3)}`;
}

function validateModelManifest(): number {
  if (!fs.existsSync(MODEL_MANIFEST_PATH)) {
    console.log("ok model manifest: skipped (no local/manifest.json — rebuild meshes into public/models/fluent/local/)");
    return 0;
  }
  const manifest = JSON.parse(fs.readFileSync(MODEL_MANIFEST_PATH, "utf8")) as {
    models?: Array<{
      guid?: string;
      name?: string;
      assetPath?: string;
      boundsMm?: { size?: unknown[] };
      unitScaleToMm?: number;
    }>;
  };
  const models = manifest.models || [];
  if (!models.length) throw new Error("No converted Fluent model metadata records were found.");
  models.forEach((model) => {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(model.guid || "")) {
      throw new Error(`Invalid model GUID: ${model.guid || "(empty)"}`);
    }
    if (!model.name) throw new Error(`Model ${model.guid} has no name.`);
    if (!/^\/models\/fluent\/local\/[0-9a-f-]+\.glb$/i.test(model.assetPath || "")) {
      throw new Error(`Model ${model.guid} has invalid asset path: ${model.assetPath || "(empty)"}. Expected /models/fluent/local/<guid>.glb`);
    }
    if (!model.unitScaleToMm || model.unitScaleToMm <= 0) {
      throw new Error(`Model ${model.guid} has invalid unitScaleToMm.`);
    }
    if (!model.boundsMm?.size?.every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0)) {
      throw new Error(`Model ${model.guid} has invalid boundsMm.size.`);
    }
  });
  return models.length;
}

function validateDirectCavityShapeParsing(): void {
  const parsed = parseFluentGeometryArtifacts([
    {
      id: "direct-cavity-shape",
      name: "direct-cavity-shape.xcmp",
      kind: "worktable-geometry",
      source: "synthetic",
      size: 0,
      text: `<?xml version="1.0"?>
<Root xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
  <Payload>
    <ObjectName>Direct Cavity Plate</ObjectName>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>11111111-2222-3333-4444-555555555555</GUID>
        <FunctionalGroup>Labware</FunctionalGroup>
        <Pipettable>
          <XNumberOfWells>2</XNumberOfWells>
          <YNumberOfWells>1</YNumberOfWells>
          <XSpacing>9</XSpacing>
          <YSpacing>9</YSpacing>
          <PositionOfFirstWell><X>1</X><Y>2</Y><Z>3</Z></PositionOfFirstWell>
          <Cavity>
            <CavityShape i:type="Cylinder">
              <Height>12.5</Height>
              <Diameter>6.2</Diameter>
            </CavityShape>
          </Cavity>
        </Pipettable>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</Root>`
    }
  ]);
  const definition = parsed.labwareDefinitions.find((item) => item.componentName === "Direct Cavity Plate");
  if (!definition) throw new Error("Direct Cavity Plate definition was not parsed.");
  if (definition.wellShape !== "round") throw new Error(`Direct Cavity Plate well shape was ${definition.wellShape || "(empty)"}.`);
  if (definition.wellDepthMm !== 12.5) throw new Error(`Direct Cavity Plate well depth was ${definition.wellDepthMm ?? "(empty)"}.`);
  if (definition.wellDiameterMm !== 6.2) throw new Error(`Direct Cavity Plate well diameter was ${definition.wellDiameterMm ?? "(empty)"}.`);
  console.log("ok direct CavityShape parsing");
}

function validateImportedLabwareDimsAndMeshes(): void {
  const meshGuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
  const parsed = parseFluentGeometryArtifacts([
    {
      key: "import-mesh-dims.xcmp",
      name: "Import Mesh Nest.xcmp",
      kind: "xcmp",
      path: "Import Mesh Nest.xcmp",
      size: 0,
      text: `<?xml version="1.0"?>
<Root xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
  <Payload>
    <ObjectName>Import Mesh Nest</ObjectName>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>11111111-2222-3333-4444-555555555555</GUID>
        <FunctionalGroup>Carrier</FunctionalGroup>
        <Dimension><X>127.76</X><Y>85.48</Y><Z>14.35</Z></Dimension>
        <Renderer>ImportNestMesh</Renderer>
        <Reference>
          <Guid>${meshGuid}</Guid>
          <TypeId>WorktableMesh</TypeId>
          <ObjectName>ImportNestMesh</ObjectName>
        </Reference>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</Root>`
    }
  ]);
  const definition = parsed.labwareDefinitions.find((item) => item.componentName === "Import Mesh Nest");
  if (!definition) throw new Error("Import Mesh Nest definition was not parsed.");
  if (definition.meshGuid !== meshGuid) {
    throw new Error(`Import Mesh Nest meshGuid was ${definition.meshGuid || "(empty)"}.`);
  }
  if (!definition.dimensionMm || definition.dimensionMm.x !== 127.76 || definition.dimensionMm.y !== 85.48) {
    throw new Error(`Import Mesh Nest dims were ${JSON.stringify(definition.dimensionMm)}.`);
  }

  const registered = registerLabwareCatalogFromDefinitions([definition]);
  if (registered < 1) throw new Error("Expected .xcmp definition registration.");
  const fromXcmp = resolveLabwareGeometry("", "Import Mesh Nest", meshGuid, "ImportNestMesh");
  if (fromXcmp.physicalWidthMm !== 127.76) {
    throw new Error(`Registered .xcmp width was ${fromXcmp.physicalWidthMm}.`);
  }
  if (resolveModelAssetPath({ meshGuid }) !== `/models/fluent/local/${meshGuid}.glb`) {
    throw new Error(`Expected mesh GUID asset path, got ${resolveModelAssetPath({ meshGuid })}.`);
  }

  const catalogMesh = "ffffffff-1111-2222-3333-444444444444";
  registerLabwareCatalogPayload({
    schema_version: "1",
    entries: [
      {
        name: "Catalog Only Plate",
        mesh_guid: catalogMesh,
        mesh_names: ["CatalogOnlyMesh"],
        physical_width_mm: 110,
        physical_depth_mm: 75,
        physical_height_mm: 20,
        rows: 8,
        cols: 12,
        pitch_x_mm: 9,
        pitch_y_mm: 9
      }
    ]
  });
  const fromCatalog = resolveLabwareGeometry("Catalog Only Plate", "Catalog Only Plate", catalogMesh, "CatalogOnlyMesh");
  if (fromCatalog.physicalWidthMm !== 110 || fromCatalog.rows !== 8 || fromCatalog.cols !== 12) {
    throw new Error(`Catalog import dims/wells wrong: ${JSON.stringify(fromCatalog)}`);
  }
  if (!fromCatalog.meshGuidHints?.includes(catalogMesh)) {
    throw new Error("Catalog import missing meshGuidHints.");
  }

  const bannedStatic = [
    "CapHolder_long",
    "CapHolder_long_44mm",
    "1x10 50ml Falcon Tube Runner",
    "1x12 Caps 50ml Falcon Tube Runner no Tubes",
    "1x16 15ml Falcon Tube Runner",
    "50ml Falcon",
    "15ml Falcon",
    "Adapter A200",
    "61mm Nest",
    "61mm Regrip Nest",
    "Resolvex A200"
  ];
  const staticNames = new Set(listLabwareGeometryCatalog().map((spec) => spec.name));
  for (const name of bannedStatic) {
    if (staticNames.has(name)) {
      throw new Error(`Static labware catalog still ships invent entry: ${name}`);
    }
    const unresolved = resolveLabwareGeometry(name, `${name}[001]`);
    if (unresolved.source === "catalog") {
      throw new Error(`Expected inferred placeholder for ${name} without ZEIA, got catalog source.`);
    }
    if (unresolved.physicalWidthMm === 428.2 || unresolved.physicalDepthMm === 398.58) {
      throw new Error(`Invented stock dims for ${name}: ${unresolved.physicalWidthMm}x${unresolved.physicalDepthMm}`);
    }
  }

  // Soft SBS templates must not ship footprint/volume as product law.
  for (const soft of ["96 Well Flat", "384 Well Plate", "24 Well Plate", "Reagent Reservoir"]) {
    const listed = listLabwareGeometryCatalog().find((spec) => spec.name === soft);
    if (!listed) throw new Error(`Missing soft template listing: ${soft}`);
    if (listed.physicalWidthMm !== 0 || listed.physicalDepthMm !== 0 || listed.maxVolumeUl !== 0) {
      throw new Error(`Soft template ${soft} still ships dims/volume: ${JSON.stringify(listed)}`);
    }
    const resolved = resolveLabwareGeometry(soft, `${soft}[001]`);
    if (resolved.source === "catalog" && resolved.name === soft && Math.abs(resolved.physicalWidthMm - 127.8) < 0.01) {
      throw new Error(`resolveLabwareGeometry still returns SBS soft dims for ${soft}`);
    }
    if (Math.abs(resolved.physicalWidthMm - 127.8) < 0.01 || Math.abs(resolved.physicalDepthMm - 85.5) < 0.01) {
      throw new Error(`SBS soft dims still applied for ${soft}: ${resolved.physicalWidthMm}x${resolved.physicalDepthMm}`);
    }
  }

  registerLabwareCatalogPayload({
    schema_version: "1",
    entries: [
      {
        name: "CapHolder_long_44mm",
        aliases: ["CapHolder_long_44mm[001]"],
        physical_width_mm: 428.2,
        physical_depth_mm: 85.6,
        physical_height_mm: 44,
        rows: 1,
        cols: 12,
        pitch_x_mm: 38,
        pitch_y_mm: 0
      }
    ]
  });
  const fromImport = resolveLabwareGeometry("CapHolder_long_44mm", "CapHolder_long_44mm[001]");
  if (fromImport.source !== "catalog" || fromImport.physicalHeightMm !== 44 || fromImport.physicalWidthMm !== 428.2) {
    throw new Error(`Import CapHolder dims lost: ${JSON.stringify(fromImport)}`);
  }
  console.log("ok imported labware dims/meshes from .xcmp + labware_catalog.json");
}
