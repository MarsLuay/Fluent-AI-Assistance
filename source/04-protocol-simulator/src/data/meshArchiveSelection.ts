const EMPTY_GUID = "00000000-0000-0000-0000-000000000000";
const WORKTABLE_MESH_REFERENCE_PATTERN =
  /<Reference>[\s\S]*?<Guid>\s*([^<]+)\s*<\/Guid>[\s\S]*?<TypeId>\s*WorktableMesh\s*<\/TypeId>[\s\S]*?<\/Reference>/gi;

export type ArchiveEntryName = {
  name: string;
};

/**
 * Load only worktable meshes referenced by parsed geometry/script artifacts.
 * Unreferenced meshes are resolved lazily from the public fluent registry + GLB library.
 */
export function selectArchiveMeshEntries<T extends ArchiveEntryName>(
  entries: T[],
  referencedMeshGuids: Set<string>,
  excludedEntryNames: Iterable<string> = []
): T[] {
  const excludedNames = new Set(Array.from(excludedEntryNames, normalizeArchiveName));
  const meshEntries = sortArchiveEntries(
    entries.filter((entry) => isWorktableMeshEntry(entry.name) && !excludedNames.has(normalizeArchiveName(entry.name)))
  );

  if (!meshEntries.length || !referencedMeshGuids.size) return [];

  return dedupeArchiveEntries(
    meshEntries.filter((entry) => referencedMeshGuids.has(normalizeGuid(stemForPath(entry.name))))
  );
}

export function referencedMeshGuidsFromTexts(texts: string[]): Set<string> {
  const out = new Set<string>();
  texts.forEach((text) => {
    for (const match of text.matchAll(WORKTABLE_MESH_REFERENCE_PATTERN)) {
      const guid = normalizeGuid(match[1] || "");
      if (isRealGuid(guid)) out.add(guid);
    }
  });
  return out;
}

export function isWorktableMeshEntry(name: string): boolean {
  return extensionForPath(name) === ".xmsh";
}

export function isWorktableGeometryEntry(name: string): boolean {
  const extension = extensionForPath(name);
  return extension === ".xwsp" || extension === ".xcmp" || extension === ".xcon" || extension === ".xsit";
}

export function normalizeGuid(value: string): string {
  const match = value.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  return match?.[0].toLowerCase() || "";
}

function sortArchiveEntries<T extends ArchiveEntryName>(entries: T[]): T[] {
  return [...entries].sort((a, b) => a.name.localeCompare(b.name));
}

function dedupeArchiveEntries<T extends ArchiveEntryName>(entries: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  entries.forEach((entry) => {
    const key = normalizeArchiveName(entry.name);
    if (seen.has(key)) return;
    seen.add(key);
    out.push(entry);
  });
  return out;
}

function normalizeArchiveName(name: string): string {
  return name.replace(/\\/g, "/").toLowerCase();
}

function isRealGuid(value: string): boolean {
  return Boolean(value && value !== EMPTY_GUID);
}

function stemForPath(name: string): string {
  const cleanName = name.split(/[\\/]/).pop() || name;
  const index = cleanName.lastIndexOf(".");
  return index >= 0 ? cleanName.slice(0, index) : cleanName;
}

function extensionForPath(name: string): string {
  const cleanName = name.split(/[\\/]/).pop() || name;
  const index = cleanName.lastIndexOf(".");
  return index >= 0 ? cleanName.slice(index).toLowerCase() : "";
}
