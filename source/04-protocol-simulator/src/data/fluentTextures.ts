import * as THREE from "three";
import type { FluentAssetRegistryEntry, FluentAssetRegistryModel, FluentTextureRef, LabwareModel } from "../types";
import { indexFluentAssetRegistry, loadFluentAssetRegistry } from "./fluentRegistry";

const textureLoader = new THREE.TextureLoader();
const textureCache = new Map<string, THREE.Texture>();
let registryIndexes: ReturnType<typeof indexFluentAssetRegistry> | null = null;

export async function ensureFluentTextureRegistry(): Promise<ReturnType<typeof indexFluentAssetRegistry> | null> {
  if (registryIndexes) return registryIndexes;
  const registry = await loadFluentAssetRegistry();
  if (!registry) return null;
  registryIndexes = indexFluentAssetRegistry(registry);
  return registryIndexes;
}

export function lookupFluentTexturesForLabware(
  registry: FluentAssetRegistryModel | null,
  labware: LabwareModel
): FluentTextureRef[] {
  if (!registry) return [];
  const indexes = indexFluentAssetRegistry(registry);
  const componentGuid = normalizeGuid(labware.transform?.componentGuid || "");
  const meshGuid = normalizeGuid(labware.transform?.meshGuid || "");
  const candidates: FluentAssetRegistryEntry[] = [];
  if (componentGuid) candidates.push(...(indexes.byComponentGuid.get(componentGuid) ?? []));
  if (meshGuid) candidates.push(...(indexes.byMeshGuid.get(meshGuid) ?? []));
  const labelKey = canonicalTextureKey(labware.label || labware.catalogName || "");
  if (labelKey) candidates.push(...(indexes.byAlias.get(labelKey) ?? []));

  const seen = new Set<string>();
  const textures: FluentTextureRef[] = [];
  const catalog = registry.textures || [];
  const catalogByName = new Map(catalog.map((row) => [canonicalTextureKey(row.objectName), row]));
  const catalogByGuid = new Map(catalog.map((row) => [normalizeGuid(row.textureGuid), row]));

  for (const entry of candidates) {
    const bindings = entry.textures?.length
      ? entry.textures
      : (entry.textureIds || []).map((textureId) => {
          const guid = normalizeGuid(textureId);
          const catalogRow = (guid && catalogByGuid.get(guid)) || catalogByName.get(canonicalTextureKey(textureId));
          return {
            textureId,
            textureGuid: catalogRow?.textureGuid || guid || null,
            position: "TOP",
            assetPath: catalogRow?.assetPath || null,
            format: catalogRow?.format || null
          } satisfies FluentTextureRef;
        });

    for (const texture of bindings) {
      if (!texture.assetPath) continue;
      const key = `${texture.textureGuid || ""}:${texture.textureId || ""}:${texture.position}`;
      if (seen.has(key)) continue;
      seen.add(key);
      textures.push(texture);
    }
  }
  return textures;
}

export function applyFluentTextureDecals(
  target: THREE.Object3D,
  textures: FluentTextureRef[],
  size?: { width: number; depth: number; height?: number }
): void {
  if (!textures.length) return;
  const bounds = new THREE.Box3().setFromObject(target);
  if (bounds.isEmpty() && size) {
    bounds.set(
      new THREE.Vector3(-size.width / 2, 0, -size.depth / 2),
      new THREE.Vector3(size.width / 2, size.height ?? 1, size.depth / 2)
    );
  }
  if (bounds.isEmpty()) return;

  const width = bounds.max.x - bounds.min.x;
  const depth = bounds.max.z - bounds.min.z;
  const topY = bounds.max.y + 0.004;

  textures.forEach((textureRef, index) => {
    if (!textureRef.assetPath) return;
    const position = (textureRef.position || "TOP").toUpperCase();
    if (position !== "TOP") return;
    const map = loadCachedTexture(textureRef.assetPath);
    if (!map) return;

    const decalWidth = Math.max(width * 0.96, 0.2);
    const decalDepth = Math.max(depth * 0.96, 0.2);
    const geometry = new THREE.PlaneGeometry(decalWidth, decalDepth);
    const material = new THREE.MeshBasicMaterial({
      map,
      transparent: true,
      depthWrite: false,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -1
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = `fluent-texture-${textureRef.textureGuid || textureRef.textureId || index}`;
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.set((bounds.min.x + bounds.max.x) / 2, topY, (bounds.min.z + bounds.max.z) / 2);
    mesh.renderOrder = 4;
    target.add(mesh);
  });
}

function loadCachedTexture(assetPath: string): THREE.Texture | null {
  const normalized = assetPath.startsWith("/") ? assetPath : `/${assetPath.replace(/^\/+/, "")}`;
  const cached = textureCache.get(normalized);
  if (cached) return cached;
  try {
    const texture = textureLoader.load(normalized);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    texture.userData.persistent = true;
    textureCache.set(normalized, texture);
    return texture;
  } catch {
    return null;
  }
}

function normalizeGuid(value: string): string {
  const trimmed = value.trim().toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(trimmed) ? trimmed : "";
}

function canonicalTextureKey(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
}
