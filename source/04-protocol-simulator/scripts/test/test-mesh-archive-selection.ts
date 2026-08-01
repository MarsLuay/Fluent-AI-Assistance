import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  referencedMeshGuidsFromTexts,
  selectArchiveMeshEntries
} from "../../src/data/meshArchiveSelection";
import { meshInventoryItemsFromRegistry } from "../../src/data/fluentRegistry";
import type { FluentAssetRegistryModel } from "../../src/types";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const localRegistryPath = path.join(
  projectRoot,
  "source/04-protocol-simulator/public/models/fluent/local/registry.json"
);

function entry(name: string) {
  return { name };
}

const referenced = new Set(["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]);
const archiveEntries = [
  entry("DataStore/SystemSpecific/Worktable/Meshes/11111111-1111-1111-1111-111111111111.xmsh"),
  entry("DataStore/SystemSpecific/Worktable/Meshes/22222222-2222-2222-2222-222222222222.xmsh"),
  ...Array.from({ length: 300 }, (_, index) =>
    entry(`DataStore/SystemSpecific/Worktable/Meshes/${String(index).padStart(8, "0")}-0000-0000-0000-000000000001.xmsh`)
  )
];

const selected = selectArchiveMeshEntries(archiveEntries, referenced, []);
assert.equal(selected.length, 2, "referenced archive meshes should not be capped");

const unreferencedOnly = selectArchiveMeshEntries(archiveEntries, new Set(), []);
assert.equal(unreferencedOnly.length, 0, "unreferenced archive meshes should defer to registry lazy loading");

const geometryText = `
  <Reference>
    <Guid>33333333-3333-3333-3333-333333333333</Guid>
    <TypeId>WorktableMesh</TypeId>
  </Reference>
`;
const guids = referencedMeshGuidsFromTexts([geometryText]);
assert.ok(guids.has("33333333-3333-3333-3333-333333333333"));

if (fs.existsSync(localRegistryPath)) {
  const registry = JSON.parse(fs.readFileSync(localRegistryPath, "utf8")) as FluentAssetRegistryModel;
  const inventory = meshInventoryItemsFromRegistry(registry);
  if (registry.sources?.installSourceType === "stub" || inventory.length === 0) {
    assert.equal(inventory.length, 0, "unusable local registry should have empty inventory");
  } else {
    assert.ok(inventory.length >= 1, `local host registry should expose mesh inventory, got ${inventory.length}`);
    assert.ok(inventory.every((item) => item.assetPath.startsWith("/models/fluent/")));
  }
}

console.log("ok mesh archive selection and registry inventory");
