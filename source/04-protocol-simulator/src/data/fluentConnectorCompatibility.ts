import type {
  FluentAssetRegistryEntry,
  FluentAssetRegistryModel,
  FluentCompatibilityCheck,
  FluentConnectorGraphModel,
  FluentSnapAnchor
} from "../types";
import { loadFluentJson } from "./loadFluentJson";

/**
 * Per-machine connector graph (gitignored under public/models/fluent/local/).
 * Rebuild-only Snap/.xcon dump — not product law. Prefer package
 * `connector_graph.json` from ZEIA; missing local file → null.
 */
export const CONNECTOR_GRAPH_LOCAL_PATH = "/models/fluent/local/connector-graph.json";

/** @deprecated Use CONNECTOR_GRAPH_LOCAL_PATH. Stub path removed. */
export const CONNECTOR_GRAPH_PATH = CONNECTOR_GRAPH_LOCAL_PATH;

let cachedConnectorGraph: FluentConnectorGraphModel | null = null;

export async function loadFluentConnectorGraph(path?: string): Promise<FluentConnectorGraphModel | null> {
  if (cachedConnectorGraph) return cachedConnectorGraph;
  if (path) {
    const loaded = await loadFluentJson(path, isFluentConnectorGraphModel);
    cachedConnectorGraph = usableConnectorGraph(loaded);
    return cachedConnectorGraph;
  }
  const local = await loadFluentJson(CONNECTOR_GRAPH_LOCAL_PATH, isFluentConnectorGraphModel);
  cachedConnectorGraph = usableConnectorGraph(local);
  return cachedConnectorGraph;
}

/** Inject ZEIA/package-derived graph (overrides empty cache; never accepts stubs). */
export function registerFluentConnectorGraph(payload: unknown): boolean {
  if (!isFluentConnectorGraphModel(payload)) return false;
  const usable = usableConnectorGraph(payload);
  if (!usable) return false;
  cachedConnectorGraph = usable;
  return true;
}

export function clearFluentConnectorGraphCache(): void {
  cachedConnectorGraph = null;
}

function usableConnectorGraph(
  payload: FluentConnectorGraphModel | null | undefined
): FluentConnectorGraphModel | null {
  if (!payload) return null;
  if (connectorInstallSourceType(payload) === "stub") return null;
  const count = Number(payload.summary?.connectorCount ?? 0);
  if (Number.isFinite(count) && count > 0) return payload;
  // Allow ZEIA graphs that only ship snap anchors / checks without a connectorCount.
  const anchors = (payload as { snapAnchorsByComponent?: Record<string, unknown> }).snapAnchorsByComponent;
  if (anchors && Object.keys(anchors).length > 0) return payload;
  const checks = payload.compatibilityChecks?.length ?? 0;
  return checks > 0 ? payload : null;
}

function connectorInstallSourceType(payload: FluentConnectorGraphModel): string {
  return String((payload as { sources?: { installSourceType?: string } }).sources?.installSourceType || "");
}

export function snapAnchorsFromRegistryEntry(entry: FluentAssetRegistryEntry | null | undefined): FluentSnapAnchor[] {
  return entry?.snapAnchors ?? [];
}

export function compatibleChildGuidsForSite(
  registry: FluentAssetRegistryModel | null,
  parentComponentGuid: string,
  siteGuid: string
): string[] {
  if (!registry) return [];
  const parent = normalizeGuid(parentComponentGuid);
  const site = normalizeGuid(siteGuid);
  const entry = registry.entries.find((row) => normalizeGuid(row.componentGuid) === parent);
  const anchor = entry?.snapAnchors?.find((row) => normalizeGuid(row.siteGuid) === site);
  return anchor?.compatibleChildGuids ?? [];
}

export function registryConnectorCompatible(
  registry: FluentAssetRegistryModel | null,
  parentComponentGuid: string,
  siteGuid: string,
  draggedComponentGuid: string
): boolean {
  const allowed = compatibleChildGuidsForSite(registry, parentComponentGuid, siteGuid);
  const child = normalizeGuid(draggedComponentGuid);
  return Boolean(child && allowed.some((guid) => normalizeGuid(guid) === child));
}

export function registryConnectorCompatibleByParentLabel(
  registry: FluentAssetRegistryModel | null,
  parentLabel: string,
  siteGuid: string,
  draggedComponentGuid: string
): boolean {
  if (!registry) return false;
  const key = canonicalKey(parentLabel);
  const entry =
    registry.entries.find((row) => canonicalKey(row.componentName || "") === key) ||
    registry.entries.find((row) => row.aliases.some((alias) => canonicalKey(alias) === key));
  if (!entry?.componentGuid) return false;
  return registryConnectorCompatible(registry, entry.componentGuid, siteGuid, draggedComponentGuid);
}

export function evaluateCompatibilityChecks(
  registry: FluentAssetRegistryModel | null,
  graph: FluentConnectorGraphModel | null
): FluentCompatibilityCheck[] {
  if (registry?.compatibilityChecks?.length) return registry.compatibilityChecks;
  return graph?.compatibilityChecks ?? [];
}

export function mergeCompatibleGuids(
  official: string[] | undefined,
  registryGuids: string[]
): string[] {
  const merged = new Set<string>();
  (official || []).forEach((guid) => {
    const normalized = normalizeGuid(guid);
    if (normalized) merged.add(normalized);
  });
  registryGuids.forEach((guid) => {
    const normalized = normalizeGuid(guid);
    if (normalized) merged.add(normalized);
  });
  return Array.from(merged);
}

function normalizeGuid(value: string | null | undefined): string {
  const trimmed = (value || "").trim().toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(trimmed) ? trimmed : "";
}

function canonicalKey(value: string): string {
  return value.trim().toLowerCase();
}

function isFluentConnectorGraphModel(payload: unknown): payload is FluentConnectorGraphModel {
  return Boolean(payload) && typeof payload === "object" && (payload as { kind?: unknown }).kind === "fluent-connector-graph";
}
