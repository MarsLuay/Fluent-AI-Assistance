import {
  registerLabwareGeometrySpec,
  resolveLabwareGeometry,
  inferCatalogNameFromLabel,
  resolveModelAssetPath,
  modelAssetPathForMeshGuid,
  searchCatalogObjectLibraryItems,
  resolveCatalogObjectLibraryItem,
  listCatalogObjectLibraryItems
} from "../../src/data/labwareCatalog";

function assert(condition: boolean, message: string) {
  if (!condition) throw new Error(message);
}

function expectEqual(actual: any, expected: any, message: string) {
  if (actual !== expected) throw new Error(`${message}: Expected ${expected}, got ${actual}`);
}

// 1. Setup mock data
registerLabwareGeometrySpec({
  name: "Super Custom Test Plate 96",
  aliases: ["SuperCustomTestPlate96"],
  physicalWidthMm: 127.76,
  physicalDepthMm: 85.48,
  physicalHeightMm: 14.35,
  rows: 8,
  cols: 12,
  pitchXMm: 9,
  pitchYMm: 9,
  wellDiameterMm: 7,
  wellDepthMm: 11,
  maxVolumeUl: 350,
  deadVolumeUl: 10,
  shape: "round",
  meshGuidHints: ["12345678-1234-1234-1234-1234567890ab"],
  modelAssetPath: "models/test-plate.glb",
  meshNameHints: ["Test_Plate_Mesh"],
  functionalGroup: "Labware.Microplate"
});

// 2. Test inferCatalogNameFromLabel
expectEqual(inferCatalogNameFromLabel("SuperCustomTestPlate96"), "Super Custom Test Plate 96", "Should infer name from alias");
expectEqual(inferCatalogNameFromLabel("Some Plate [123]"), "Some Plate", "Should strip fluent instance suffix");
expectEqual(inferCatalogNameFromLabel(""), "Generic Fluent Component", "Should fallback to generic component name");

// 3. Test resolveLabwareGeometry
const resolvedByName = resolveLabwareGeometry("Super Custom Test Plate 96", "");
expectEqual(resolvedByName.source, "catalog", "Should resolve by name");
expectEqual(resolvedByName.name, "Super Custom Test Plate 96", "Should resolve by name");

const resolvedByAlias = resolveLabwareGeometry("", "SuperCustomTestPlate96");
expectEqual(resolvedByAlias.source, "catalog", "Should resolve by alias");
expectEqual(resolvedByAlias.name, "Super Custom Test Plate 96", "Should resolve by alias");

const resolvedByGuid = resolveLabwareGeometry("Unknown", "Unknown", "12345678-1234-1234-1234-1234567890ab");
expectEqual(resolvedByGuid.source, "catalog", "Should resolve by mesh guid hint");
expectEqual(resolvedByGuid.name, "Super Custom Test Plate 96", "Should resolve by mesh guid hint");

const resolvedByMeshName = resolveLabwareGeometry("Unknown", "Unknown", "", "Test_Plate_Mesh");
expectEqual(resolvedByMeshName.source, "catalog", "Should resolve by mesh name hint");
expectEqual(resolvedByMeshName.name, "Super Custom Test Plate 96", "Should resolve by mesh name hint");

const inferred = resolveLabwareGeometry("Unknown Plate", "Unknown Plate");
expectEqual(inferred.source, "inferred", "Should infer unknown plate");

// 4. Test resolveModelAssetPath
expectEqual(resolveModelAssetPath({ catalogName: "Super Custom Test Plate 96" }), "models/test-plate.glb", "Should return asset path for known catalog name");
expectEqual(resolveModelAssetPath({ meshGuid: "12345678-1234-1234-1234-1234567890ab" }), "/models/fluent/local/12345678-1234-1234-1234-1234567890ab.glb", "Should prefer mesh guid directly");
expectEqual(modelAssetPathForMeshGuid("12345678-1234-1234-1234-1234567890ab"), "/models/fluent/local/12345678-1234-1234-1234-1234567890ab.glb", "Should return fluent asset path for valid guid");
expectEqual(modelAssetPathForMeshGuid("invalid-guid"), "", "Should return empty string for invalid guid");

// 5. Test searchCatalogObjectLibraryItems
// Note: test items are not listed in search because it only lists LABWARE_GEOMETRY_CATALOG (SOFT_WELL_TEMPLATES + GENERIC_PLACEHOLDER)
// The test will check if searching works for standard elements
const searchResults = searchCatalogObjectLibraryItems("96");
assert(searchResults.length > 0, "Should find 96-well plate in search");

const allItems = listCatalogObjectLibraryItems();
assert(allItems.length > 0, "Should list items from LABWARE_GEOMETRY_CATALOG");
const genericItem = allItems.find(item => item.name === "Generic Fluent Component");
assert(genericItem !== undefined, "Should find generic placeholder in library items");

console.log("ok labware catalog full API tests");
