"""Labware label/catalog contracts derived from an imported ZEIA.

Do not hardcode site or vendor-variant pairings here (for example AdapterA200 →
ElutionRack). Preferred catalogs come from worktable placements / labware
catalog mined from the full export. Without that map, checks are no-ops.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .labware_catalog_export import (
    alias_maps_from_labware_catalog,
    load_labware_catalog,
    strip_fluent_instance_suffix,
)

LABEL_CATALOG_MISMATCH_CODE = "label_catalog_mismatch"
# Backward-compatible alias used by older tests/call sites.
A200_ADAPTER_CATALOG_CODE = LABEL_CATALOG_MISMATCH_CODE
A200_ADAPTER_LABEL = "AdapterA200"
# Deprecated: never invent a required catalog. Prefer ZEIA preferred maps.
A200_ADAPTER_REQUIRED_CATALOG = ""
A200_ADAPTER_PLAIN_CATALOG = ""


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def labware_base_label(label: Any) -> str:
    """Strip Fluent instance suffixes like ``[001]`` / ``[platecount]``."""
    return strip_fluent_instance_suffix(label)


def entry_catalog(entry: Mapping[str, Any] | None = None, *, catalog: Any = None) -> str:
    if entry is not None:
        return str(entry.get("catalog") or entry.get("labware_type") or "").strip()
    return str(catalog or "").strip()


# Backward-compatible name used by older imports.
a200_adapter_catalog = entry_catalog


def is_a200_adapter_label(label: Any) -> bool:
    """Legacy helper. Prefer ZEIA preferred-catalog maps over label heuristics."""
    base = _norm(labware_base_label(label)).replace(" ", "").replace("_", "")
    return base == "adaptera200"


def preferred_label_catalogs_from_geometry(
    geometry: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return ``{normalized_base_label: catalog}`` when ZEIA placements agree.

    Only unanimous label→catalog pairs are returned so mixed decks do not invent
    a requirement.
    """
    geometry = geometry if isinstance(geometry, Mapping) else {}
    catalogs_by_label: dict[str, set[str]] = {}
    for workspace in geometry.get("workspaces") or []:
        if not isinstance(workspace, Mapping):
            continue
        for placement in workspace.get("placements") or []:
            if not isinstance(placement, Mapping):
                continue
            label = labware_base_label(placement.get("label") or placement.get("name") or "")
            catalog = str(
                placement.get("catalog")
                or placement.get("labware_type")
                or placement.get("component_name")
                or ""
            ).strip()
            if not label or not catalog:
                continue
            catalogs_by_label.setdefault(_norm(label), set()).add(catalog)
    preferred: dict[str, str] = {}
    for key, catalogs in catalogs_by_label.items():
        if len(catalogs) == 1:
            preferred[key] = next(iter(catalogs))
    return preferred


def preferred_label_catalogs_from_labware_catalog(
    catalog: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Prefer component type names for instance/alias labels from ``labware_catalog.json``."""
    maps = alias_maps_from_labware_catalog(catalog)
    preferred: dict[str, str] = {}
    for source_map in (maps.get("labware_aliases") or {}, maps.get("catalog_aliases") or {}):
        for alias, target in source_map.items():
            bare = labware_base_label(alias)
            target_text = str(target or "").strip()
            if not bare or not target_text:
                continue
            if _norm(bare) == _norm(target_text):
                continue
            preferred.setdefault(_norm(bare), target_text)
    return preferred


def preferred_label_catalogs_from_manifest(
    manifest: Mapping[str, Any] | None,
    *,
    labware_catalog: Mapping[str, Any] | None = None,
    context_root: Path | str | None = None,
) -> dict[str, str]:
    """Merge placement truth with optional ``labware_catalog.json`` alias hints."""
    preferred = preferred_label_catalogs_from_geometry(
        manifest.get("worktable_geometry") if isinstance(manifest, Mapping) else None
    )
    catalog_doc = labware_catalog
    if catalog_doc is None and context_root is not None:
        catalog_doc = load_labware_catalog(Path(context_root) / "labware_catalog.json")
    for key, catalog in preferred_label_catalogs_from_labware_catalog(catalog_doc).items():
        preferred.setdefault(key, catalog)
    return preferred


def resolve_preferred_label_catalogs(
    *,
    spec: Mapping[str, Any] | None = None,
    context: Any = None,
    manifest: Mapping[str, Any] | None = None,
    labware_catalog: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Best-effort preferred map from explicit inputs or ``source.context``."""
    if isinstance(manifest, Mapping):
        root = getattr(context, "root", None) if context is not None else None
        return preferred_label_catalogs_from_manifest(
            manifest,
            labware_catalog=labware_catalog,
            context_root=root,
        )
    if context is not None:
        ctx_manifest = getattr(context, "manifest", None)
        if isinstance(ctx_manifest, Mapping):
            return preferred_label_catalogs_from_manifest(
                ctx_manifest,
                labware_catalog=labware_catalog,
                context_root=getattr(context, "root", None),
            )
    if not isinstance(spec, Mapping):
        return {}
    source = spec.get("source") if isinstance(spec.get("source"), Mapping) else {}
    context_name = str(source.get("context") or "").strip()
    if not context_name:
        return {}
    try:
        from .project_context import load_project

        loaded = load_project(context_name)
    except Exception:
        return {}
    return preferred_label_catalogs_from_manifest(
        getattr(loaded, "manifest", None),
        labware_catalog=labware_catalog,
        context_root=getattr(loaded, "root", None),
    )


def label_catalog_mismatch_message(
    *,
    label: Any,
    catalog: Any = "",
    expected: Any = "",
) -> str:
    actual = str(catalog or "").strip() or "<empty>"
    expected_text = str(expected or "").strip() or "<unknown ZEIA catalog>"
    base = labware_base_label(label) or str(label or "").strip() or "<labware>"
    return (
        f"{base} must use labware type {expected_text} from the imported ZEIA "
        f"(got {actual!r}). Copy the exact catalog/type string from the source "
        "worktable or script in the full export."
    )


def label_catalog_issue(
    *,
    label: Any,
    catalog: Any,
    path: str,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    preferred = preferred_label_catalogs or {}
    key = _norm(labware_base_label(label))
    if not key:
        return None
    expected = preferred.get(key)
    if not expected:
        return None
    if _norm(catalog) == _norm(expected):
        return None
    return {
        "path": path,
        "code": LABEL_CATALOG_MISMATCH_CODE,
        "message": label_catalog_mismatch_message(label=label, catalog=catalog, expected=expected),
        "actual": str(catalog or "").strip(),
        "expected": expected,
    }


def ir_label_catalog_issues(
    ir: Mapping[str, Any] | None,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    if not isinstance(ir, Mapping):
        return []
    preferred = preferred_label_catalogs or {}
    if not preferred:
        return []
    issues: list[dict[str, str]] = []
    for index, item in enumerate(ir.get("labware") or []):
        if not isinstance(item, Mapping):
            continue
        issue = label_catalog_issue(
            label=item.get("label"),
            catalog=entry_catalog(item),
            path=f"labware[{index}].catalog",
            preferred_label_catalogs=preferred,
        )
        if issue is not None:
            issues.append(issue)
    return issues


def recipe_label_catalog_issues(
    recipe: Mapping[str, Any] | None,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    if not isinstance(recipe, Mapping):
        return []
    preferred = preferred_label_catalogs or {}
    if not preferred:
        return []
    issues: list[dict[str, str]] = []
    for index, item in enumerate(recipe.get("labware") or []):
        if not isinstance(item, Mapping):
            continue
        issue = label_catalog_issue(
            label=item.get("label"),
            catalog=entry_catalog(item),
            path=f"verification_recipe.labware[{index}].catalog",
            preferred_label_catalogs=preferred,
        )
        if issue is not None:
            issues.append(issue)
    return issues


# --- Legacy A200-named wrappers (no ElutionRack invent without ZEIA map) ---


def a200_adapter_catalog_is_valid(catalog: Any) -> bool:
    """Deprecated. Without a preferred map this only checks non-empty catalog."""
    return bool(str(catalog or "").strip())


def a200_adapter_mismatch_message(
    *,
    label: Any = A200_ADAPTER_LABEL,
    catalog: Any = "",
    expected: Any = "",
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> str:
    """Build a mismatch message using the preferred ZEIA catalog only.

    ``expected`` or ``preferred_label_catalogs[label]`` must supply the catalog.
    Never falls back to a hardcoded ElutionRack / Adapter A200 string.
    """
    preferred = preferred_label_catalogs or {}
    expected_text = str(expected or "").strip()
    if not expected_text:
        key = _norm(labware_base_label(label))
        expected_text = str(preferred.get(key) or "").strip()
    if not expected_text:
        base = labware_base_label(label) or str(label or "").strip() or "<labware>"
        actual = str(catalog or "").strip() or "<empty>"
        return (
            f"{base} has catalog {actual!r} but no preferred ZEIA catalog is "
            "configured for this label. Import a full ZEIA (worktable placements / "
            "labware_catalog) or pass preferred_label_catalogs — the pipeline does "
            "not invent a required catalog for this label."
        )
    return label_catalog_mismatch_message(
        label=label,
        catalog=catalog,
        expected=expected_text,
    )


def a200_adapter_catalog_issue(
    *,
    label: Any,
    catalog: Any,
    path: str,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    preferred = dict(preferred_label_catalogs or {})
    if not preferred:
        # No ZEIA map: do not invent a required catalog.
        return None
    return label_catalog_issue(
        label=label,
        catalog=catalog,
        path=path,
        preferred_label_catalogs=preferred,
    )


def ir_a200_adapter_catalog_issues(
    ir: Mapping[str, Any] | None,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    return ir_label_catalog_issues(ir, preferred_label_catalogs)
