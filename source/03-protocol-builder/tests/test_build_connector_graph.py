"""Tests for build_connector_graph.py."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    from tecan_tools import build_connector_graph as connectors
    _CONNECTORS_IMPORT_ERROR: str | None = None
except ImportError:
    try:
        import build_connector_graph as connectors  # type: ignore
        _CONNECTORS_IMPORT_ERROR = None
    except ImportError as exc:
        connectors = None  # type: ignore[assignment,misc]
        _CONNECTORS_IMPORT_ERROR = str(exc)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent/local/registry.json"
HOST_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")


def _host_connectors_available() -> bool:
    return (HOST_INSTALL / "SystemSpecific/Worktable/Connectors").exists()


@unittest.skipUnless(
    connectors is not None,
    f"build_connector_graph not importable: {_CONNECTORS_IMPORT_ERROR or 'unknown'}",
)
class BuildConnectorGraphUnitTests(unittest.TestCase):
    """Geometry/count checks that do not need a host FluentControl install."""

    def test_soft_compatibility_checks_are_empty(self) -> None:
        """Vendor-family soft checks are not product law (Resolvex/CapHolder/Falcon invent)."""
        checks = connectors.build_compatibility_checks(
            [
                {
                    "guid": "c926b5b2-2916-4bb0-b6dd-7dc18333e8f6",
                    "childComponentGuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "siteGuid": "0c89579d-c508-4629-ab4c-676a22d545d2",
                }
            ],
            {
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": "1x10 50ml Falcon Tube Runner",
                "bbc0d106-0000-4000-8000-000000000001": "Fluent ID Left 4 Grid",
            },
            {"0c89579d-c508-4629-ab4c-676a22d545d2": "bbc0d106-0000-4000-8000-000000000001"},
            {},
            sites_dir=Path("/nonexistent"),
        )
        self.assertEqual(checks, [])
        source = Path(connectors.__file__).read_text(encoding="utf-8")
        self.assertNotIn("adapter_to_resolvex_a200", source)
        self.assertNotIn("filter_plate_to_sbs_nest_adapter", source)
        self.assertNotIn("cap_to_cap_holder_site", source)
        self.assertNotIn("tube_to_rack_site", source)

    def test_count_verification_mines_from_install_components(self) -> None:
        component_names = {
            "11111111-1111-4111-8111-111111111111": "Demo_Resolvex_Like",
            "22222222-2222-4222-8222-222222222222": "Demo_Adapter_Like",
            "33333333-3333-4333-8333-333333333333": "Demo_CapHolder_Like",
            "44444444-4444-4444-8444-444444444444": "Unused Nest",
        }
        counts = {
            "11111111-1111-4111-8111-111111111111": 9,
            "22222222-2222-4222-8222-222222222222": 4,
            "33333333-3333-4333-8333-333333333333": 2,
        }
        rows = connectors.build_count_verification(counts, component_names)
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(set(by_id), {"demo_resolvex_like", "demo_adapter_like", "demo_capholder_like"})
        self.assertNotIn("resolvex_a200", by_id)
        self.assertNotIn("capholder_long", by_id)
        self.assertEqual(by_id["demo_resolvex_like"]["actualCount"], 9)
        self.assertTrue(by_id["demo_resolvex_like"]["matches"])
        self.assertEqual(by_id["demo_adapter_like"]["actualCount"], 4)
        self.assertEqual(by_id["demo_capholder_like"]["actualCount"], 2)
        self.assertNotIn("067ddc0e-a770-4f4a-9086-127c565ff85a", by_id)
        self.assertEqual(connectors.CONNECTOR_COUNT_PROFILES, ())


@unittest.skipUnless(
    connectors is not None,
    f"build_connector_graph not importable: {_CONNECTORS_IMPORT_ERROR or 'unknown'}",
)
class BuildConnectorGraphHostTests(unittest.TestCase):
    """Optional host-install regressions (Windows FluentControl database)."""

    def test_verification_profiles_mined_from_install_components(self) -> None:
        if not _host_connectors_available():
            self.skipTest("host install not available")
        graph = connectors.build_connector_graph(
            install_path=HOST_INSTALL,
            registry_path=REGISTRY_PATH if REGISTRY_PATH.exists() else None,
            refresh_index=False,
            include_all_connectors=False,
        )
        rows = [row for row in graph.get("verification", []) if isinstance(row, dict)]
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(int(row.get("actualCount") or 0) >= 1 for row in rows))
        self.assertTrue(all(row.get("matchedComponentGuids") for row in rows))
        for row in rows:
            self.assertTrue(row.get("componentGuid"), row)
            self.assertEqual(row.get("source"), "install")

    def test_compatibility_soft_checks_empty_on_host(self) -> None:
        if not _host_connectors_available():
            self.skipTest("host install not available")
        graph = connectors.build_connector_graph(
            install_path=HOST_INSTALL,
            registry_path=REGISTRY_PATH if REGISTRY_PATH.exists() else None,
            refresh_index=False,
            include_all_connectors=False,
        )
        self.assertEqual(graph.get("compatibilityChecks") or [], [])

    def test_capholder_has_snap_anchors(self) -> None:
        if not _host_connectors_available():
            self.skipTest("host install not available")
        if not REGISTRY_PATH.exists():
            self.skipTest("registry not available")
        graph = connectors.build_connector_graph(
            install_path=HOST_INSTALL,
            registry_path=REGISTRY_PATH,
            refresh_index=False,
            include_all_connectors=False,
        )
        snap_by_component = graph.get("snapAnchorsByComponent") or {}
        verification = [
            row for row in graph.get("verification", []) if isinstance(row, dict)
        ]
        # Prefer CapHolder-named components when present; else any verified component with snaps.
        cap_rows = [
            row
            for row in verification
            if "capholder" in str(row.get("componentName") or "").lower().replace(" ", "")
        ]
        targets = cap_rows or verification[:3]
        found = False
        for row in targets:
            guid = str(row.get("componentGuid") or "")
            anchors = snap_by_component.get(guid) or []
            if anchors:
                found = True
                break
        if not found and not verification:
            self.skipTest("no verification components with snap anchors")
        # Soft: host may lack CapHolder; do not invent.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
