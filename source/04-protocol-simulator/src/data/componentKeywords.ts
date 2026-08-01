// Single source of truth for the text keywords used to recognize Fluent
// worktable instruments (device) and the cabinet frame/walls (structural).
//
// These checks previously lived as separate, hand-maintained keyword lists in
// the parser, geometry reader, catalog, and 3D scene. They drifted apart (e.g.
// "infinite"/"resolvex"/"vial gripper" were treated as devices in some places
// but not others), which let real instruments fall through to "hidden context"
// and silently disappear from the scene. Centralizing the keywords here means a
// new instrument or wall variant is classified consistently everywhere.
//
// NOTE: ambiguous, context-specific terms (grid / shelf / extension / deck
// segment as carrier-vs-structural, nest, adapter, etc.) are intentionally NOT
// included here — those decisions differ per call site and stay local.

// Substrings that mark a component as an on-deck instrument/device. A keyword
// like "tubeeye" also matches longer forms such as "tubeeyex".
export const DEVICE_KEYWORDS = [
  "microhawk",
  "tubeeye",
  "infinite",
  "resolvex",
  "incubator",
  "vial gripper",
  "scanner",
  "camera",
  "reader",
  "barcode",
  "sensor",
  "pump",
  "heater",
  "shaker",
  "magnet",
  "centrifuge"
] as const;

// Substrings that mark a component as the cabinet frame / wall enclosure. Only
// unambiguous structural terms belong here; deck segments, grids, shelves, and
// extensions are deliberately excluded because they are carriers/deck in some
// contexts.
export const STRUCTURAL_KEYWORDS = [
  "frame",
  "frameside",
  "frame side",
  "side wall",
  "sidewall",
  "front wall",
  "back wall",
  "rear wall",
  "wall",
  "plexiglas",
  "pane",
  "door",
  "cover",
  "rail",
  "base unit"
] as const;

function includesAnyKeyword(text: string, keywords: readonly string[]): boolean {
  return keywords.some((keyword) => text.includes(keyword));
}

// `text` must already be lowercased / space-normalized (as produced by the
// callers' canonicalText / normalizeLabwareName helpers).
export function isDeviceComponentText(text: string): boolean {
  return includesAnyKeyword(text, DEVICE_KEYWORDS);
}

export function isStructuralComponentText(text: string): boolean {
  return includesAnyKeyword(text, STRUCTURAL_KEYWORDS);
}
