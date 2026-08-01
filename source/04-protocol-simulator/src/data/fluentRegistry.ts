import type { FluentAssetRegistryEntry, FluentAssetRegistryModel } from "../types";
import { loadFluentJson } from "./loadFluentJson";

/**
 * Per-machine registry rebuild (gitignored under public/models/fluent/local/).
 * Rebuild-only host/ZEIA dump — not product law. Prefer package labware_catalog /
 * ZEIA mesh GUIDs when present; missing local file → null.
 */
export const FLUENT_REGISTRY_LOCAL_PATH = "/models/fluent/local/registry.json";

/** @deprecated Use FLUENT_REGISTRY_LOCAL_PATH. Stub path removed. */
export const FLUENT_REGISTRY_PATH = FLUENT_REGISTRY_LOCAL_PATH;

export type RegistryMeshInventoryItem = {
  guid: string;
  name: string;
  sourcePath: string;
  assetPath: string;
  registryBacked: true;
};

let cachedRegistry: FluentAssetRegistryModel | null = null;

export async function loadFluentAssetRegistry(path?: string): Promise<FluentAssetRegistryModel | null> {
  if (cachedRegistry) return cachedRegistry;
  if (path) {
    const loaded = await loadFluentJson(path, isFluentAssetRegistryModel);
    cachedRegistry = usableRegistry(loaded);
    return cachedRegistry;
  }
  const local = await loadFluentJson(FLUENT_REGISTRY_LOCAL_PATH, isFluentAssetRegistryModel);
  cachedRegistry = usableRegistry(local);
  return cachedRegistry;
}

export function clearFluentAssetRegistryCache(): void {
  cachedRegistry = null;
}

function usableRegistry(
  payload: FluentAssetRegistryModel | null | undefined
): FluentAssetRegistryModel | null {
  if (!payload) return null;
  if (payload.sources?.installSourceType === "stub") return null;
  const entryCount = Number(payload.summary?.entryCount ?? payload.entries?.length ?? 0);
  return Number.isFinite(entryCount) && entryCount > 0 ? payload : null;
}

export function indexFluentAssetRegistry(registry: FluentAssetRegistryModel): {
  byMeshGuid: Map<string, FluentAssetRegistryEntry[]>;
  byComponentGuid: Map<string, FluentAssetRegistryEntry[]>;
  byAlias: Map<string, FluentAssetRegistryEntry[]>;
} {
  const byMeshGuid = new Map<string, FluentAssetRegistryEntry[]>();
  const byComponentGuid = new Map<string, FluentAssetRegistryEntry[]>();
  const byAlias = new Map<string, FluentAssetRegistryEntry[]>();

  registry.entries.forEach((entry) => {
    if (entry.meshGuid) {
      const rows = byMeshGuid.get(entry.meshGuid) ?? [];
      rows.push(entry);
      byMeshGuid.set(entry.meshGuid, rows);
    }
    if (entry.componentGuid) {
      const rows = byComponentGuid.get(entry.componentGuid) ?? [];
      rows.push(entry);
      byComponentGuid.set(entry.componentGuid, rows);
    }
    entry.aliases.forEach((alias) => {
      const key = canonicalRegistryKey(alias);
      const rows = byAlias.get(key) ?? [];
      rows.push(entry);
      byAlias.set(key, rows);
    });
    [entry.objectName, entry.componentName, entry.renderer].forEach((label) => {
      if (!label) return;
      const key = canonicalRegistryKey(label);
      const rows = byAlias.get(key) ?? [];
      rows.push(entry);
      byAlias.set(key, rows);
    });
  });

  return { byMeshGuid, byComponentGuid, byAlias };
}

export function lookupFluentAssetRegistryEntry(
  registry: FluentAssetRegistryModel,
  query: { meshGuid?: string; componentGuid?: string; alias?: string }
): FluentAssetRegistryEntry | null {
  const indexes = indexFluentAssetRegistry(registry);
  if (query.meshGuid) {
    return indexes.byMeshGuid.get(query.meshGuid)?.[0] ?? null;
  }
  if (query.componentGuid) {
    return indexes.byComponentGuid.get(query.componentGuid)?.[0] ?? null;
  }
  if (query.alias) {
    return indexes.byAlias.get(canonicalRegistryKey(query.alias))?.[0] ?? null;
  }
  return null;
}

export function meshInventoryItemsFromRegistry(registry: FluentAssetRegistryModel): RegistryMeshInventoryItem[] {
  const byGuid = new Map<string, RegistryMeshInventoryItem>();
  registry.entries.forEach((entry) => {
    if (entry.sourceType === "procedural") return;
    const guid = normalizeRegistryGuid(entry.meshGuid);
    if (!guid || byGuid.has(guid)) return;
    const assetPath = fluentRegistryAssetPath(entry.assetPath, guid);
    if (!assetPath) return;
    byGuid.set(guid, {
      guid,
      name: entry.objectName || entry.componentName || guid,
      sourcePath: entry.sourcePath || `registry:${guid}`,
      assetPath,
      registryBacked: true
    });
  });
  return Array.from(byGuid.values()).sort((a, b) => a.name.localeCompare(b.name) || a.guid.localeCompare(b.guid));
}

export function mergeMeshInventoryItems<T extends { guid: string }>(artifactItems: T[], registryItems: T[]): T[] {
  const merged = new Map<string, T>();
  registryItems.forEach((item) => {
    if (item.guid) merged.set(item.guid, item);
  });
  artifactItems.forEach((item) => {
    if (item.guid) merged.set(item.guid, item);
  });
  return Array.from(merged.values()).sort((a, b) => a.guid.localeCompare(b.guid));
}

function normalizeRegistryGuid(value: string | null): string {
  const trimmed = (value || "").trim().toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(trimmed) ? trimmed : "";
}

function normalizeRegistryAssetPath(value: string | null): string {
  const trimmed = (value || "").trim();
  if (!trimmed) return "";
  return trimmed.startsWith("/") ? trimmed : `/${trimmed.replace(/^\/+/, "")}`;
}

function fluentRegistryAssetPath(assetPath: string | null, guid: string): string {
  const normalized = normalizeRegistryAssetPath(assetPath);
  if (normalized.startsWith("/models/fluent/local/")) return normalized;
  // Legacy tracked-root mesh paths → local rebuild tree.
  const legacyMesh = normalized.match(/^\/models\/fluent\/([0-9a-f-]+\.glb)$/i);
  if (legacyMesh) return `/models/fluent/local/${legacyMesh[1]}`;
  if (normalized.startsWith("/models/fluent/")) return normalized;
  return guid ? `/models/fluent/local/${guid}.glb` : "";
}

function canonicalRegistryKey(value: string): string {
  return value.trim().toLowerCase();
}

function isFluentAssetRegistryModel(payload: unknown): payload is FluentAssetRegistryModel {
  return Boolean(payload) && typeof payload === "object" && (payload as { kind?: unknown }).kind === "fluent-asset-registry";
}
