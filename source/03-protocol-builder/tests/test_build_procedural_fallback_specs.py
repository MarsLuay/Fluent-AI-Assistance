"""Tests for build_procedural_fallback_specs.py."""

from __future__ import annotations

import unittest
from pathlib import Path

import pytest

try:
    from tecan_tools import build_procedural_fallback_specs as procedural

    _PROCEDURAL_IMPORT_ERROR: str | None = None
except ImportError:
    try:
        import build_procedural_fallback_specs as procedural  # type: ignore

        _PROCEDURAL_IMPORT_ERROR = None
    except ImportError as exc:
        procedural = None  # type: ignore[assignment,misc]
        _PROCEDURAL_IMPORT_ERROR = str(exc)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Optional local ZEIA extract under ready-to-import — never a committed host dump.
_READY = PROJECT_ROOT / "source/03-protocol-builder/ready-to-import"
EXTRACTED_ROOT_CANDIDATES = sorted(
    path
    for path in _READY.glob("*/temp_files/extracted/DataStore")
    if path.is_dir()
) if _READY.is_dir() else []
REGISTRY_PATH = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent/local/registry.json"
ALIASES_PATH = PROJECT_ROOT / "source/03-protocol-builder/config/aliases/labware_aliases.yaml"


def _first_extracted_root() -> Path | None:
    return EXTRACTED_ROOT_CANDIDATES[0] if EXTRACTED_ROOT_CANDIDATES else None


def _find_component_xcmp(extracted_root: Path, name_needle: str) -> Path | None:
    """Resolve a component .xcmp by ObjectName / stem — not by host GUID."""
    components_dir = extracted_root / "SystemSpecific/Worktable/Components"
    if not components_dir.is_dir():
        return None
    needle = name_needle.casefold()
    from fluentcoder.catalog.xcmp import load_xcmp

    for path in sorted(components_dir.glob("*.xcmp")):
        try:
            component = load_xcmp(path)
        except Exception:
            continue
        name = str(getattr(component, "name", "") or path.stem)
        if needle in name.casefold():
            return path
    return None


@unittest.skipUnless(
    procedural is not None,
    f"build_procedural_fallback_specs not importable: {_PROCEDURAL_IMPORT_ERROR or 'unknown'}",
)
class BuildProceduralFallbackSpecsTests(unittest.TestCase):
    def test_cap_holder_long_spec_shape(self) -> None:
        extracted = _first_extracted_root()
        if extracted is None:
            self.skipTest("extracted ZEIA DataStore fixture not available")
        cap_holder = _find_component_xcmp(extracted, "CapHolder_long")
        if cap_holder is None:
            self.skipTest("CapHolder_long component not found in ZEIA extract (discover by name)")
        from fluentcoder.catalog.xcmp import load_xcmp

        component = load_xcmp(cap_holder)
        dims = getattr(component, "dimensions_mm", None) or getattr(component, "dimension_mm", None)
        registry_dims = None
        if dims is not None:
            registry_dims = {
                "xMm": float(getattr(dims, "x", dims[0] if isinstance(dims, (list, tuple)) else 0) or 0),
                "yMm": float(getattr(dims, "y", dims[1] if isinstance(dims, (list, tuple)) else 0) or 0),
                "zMm": float(getattr(dims, "z", dims[2] if isinstance(dims, (list, tuple)) else 0) or 0),
            }
        spec = procedural.component_to_spec(
            component,
            alias_map={},
            sites_dir=extracted / "SystemSpecific/Worktable/Sites",
            source_type="zeia",
            registry_row={
                "componentGuid": component.guid,
                "componentName": component.name,
                "dimensions": registry_dims,
            },
        )
        self.assertEqual(spec["kind"], "cap-holder")
        self.assertEqual(spec["role"], "cap_holder")
        self.assertIn("capholder", str(spec["componentName"] or "").casefold())
        self.assertEqual(spec["sites"].get("x"), 8)
        self.assertEqual(spec["sites"].get("y"), 2)
        # Nest pin names/GUIDs come from this ZEIA — do not assert Falcon50_* host labels.

    def test_cap_holder_family_from_registry_targets(self) -> None:
        extracted = _first_extracted_root()
        if not REGISTRY_PATH.exists() or extracted is None:
            self.skipTest("local registry / ZEIA extract not available")
        payload = procedural.build_procedural_specs(
            install_path=extracted,
            registry_path=REGISTRY_PATH,
            aliases_path=ALIASES_PATH if ALIASES_PATH.exists() else None,
            only_priority=True,
        )
        names = {row["componentName"] for row in payload["specs"]}
        cap_names = [name for name in names if "capholder" in str(name).casefold()]
        self.assertTrue(cap_names, f"expected CapHolder family in priority specs, got {sorted(names)[:20]}")
        # Prefer 44mm variant when present; otherwise any CapHolder_long is enough.
        cap_44 = next(
            (row for row in payload["specs"] if "44mm" in str(row.get("componentName") or "").casefold()),
            None,
        )
        if cap_44 is not None:
            self.assertEqual(cap_44["sites"].get("x"), 8)
            self.assertEqual(cap_44["sites"].get("y"), 2)

    @pytest.mark.fluentcontrol_shell
    def test_build_full_spec_manifest(self) -> None:
        if not REGISTRY_PATH.exists():
            self.skipTest("local registry not available")
        install = Path(r"C:\ProgramData\Tecan\VisionX\Database")
        if not (install / "SystemSpecific/Worktable/Components").exists():
            install = _first_extracted_root() or Path()
        if not (install / "SystemSpecific/Worktable/Components").exists():
            self.skipTest("component DataStore not available")
        payload = procedural.build_procedural_specs(
            install_path=install,
            registry_path=REGISTRY_PATH,
            aliases_path=ALIASES_PATH if ALIASES_PATH.exists() else None,
            only_priority=False,
        )
        self.assertEqual(payload["kind"], procedural.SPEC_KIND)
        self.assertGreaterEqual(payload["summary"]["specCount"], 1)
        self.assertGreater(payload["summary"]["withArrangement"], 0)


if __name__ == "__main__":
    unittest.main()
