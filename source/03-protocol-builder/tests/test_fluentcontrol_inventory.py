"""Tests for FluentControl local inventory / rewrite / prereq helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tecan_common import native_acceleration
import fluent_pipeline.fluentcontrol_inventory as inventory_module
from fluent_pipeline.fluentcontrol_inventory import (
    build_scripts_inventory,
    build_scripts_inventory_from_target_profile,
    collision_preflight,
    find_unique_guid,
    load_scripts_inventory,
    report_missing_system_dependencies,
    rewrite_script_reference_guids,
    write_scripts_inventory,
)


def _write_xscr(root: Path, guid: str, name: str, folder: str) -> None:
    (root / f"{guid}.xscr").write_text(
        (
            '<?xml version="1.0"?>\n'
            "<VxData><Payload>\n"
            f"  <ObjectName>{name}</ObjectName>\n"
            f"  <ObjectSubfolderPath>{folder}</ObjectSubfolderPath>\n"
            "  <Checksum>00</Checksum>\n"
            "</Payload></VxData>\n"
        ),
        encoding="utf-8",
    )


class FluentControlInventoryTests(unittest.TestCase):
    def test_script_name_index_uses_native_ascii_and_unicode_fallback(self) -> None:
        class CustomName(str):
            def casefold(self):
                return "custom-key"

        values = ["Demo", " Demo ", "Straße", "Z", CustomName("ignored")]
        expected = [value.casefold() for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [inventory_module._casefold_key(value) for value in values]
        self.assertEqual(disabled, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inventory_module,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                enabled = [inventory_module._casefold_key(value) for value in values]
                self.assertEqual(native_call.call_count, 3)
        self.assertEqual(enabled, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inventory_module,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback = [inventory_module._casefold_key(value) for value in values]
        self.assertEqual(fallback, expected)

        inventory = {
            "scripts": [
                {
                    "guid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "object_name": "Straße",
                    "folder": "West",
                },
                {
                    "guid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "object_name": "STRASSE",
                    "folder": "East",
                },
            ]
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled_resolution = inventory_module.resolve_local_script_guid_for_name(
                inventory, "StraSSe", preferred_folder="east"
            )
        self.assertEqual(
            disabled_resolution,
            ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "folder_unique"),
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inventory_module,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                enabled_resolution = inventory_module.resolve_local_script_guid_for_name(
                    inventory, "StraSSe", preferred_folder="east"
                )
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(enabled_resolution, disabled_resolution)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inventory_module,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback_resolution = inventory_module.resolve_local_script_guid_for_name(
                    inventory, "StraSSe", preferred_folder="east"
                )
        self.assertEqual(fallback_resolution, disabled_resolution)

    def test_first_xml_text_uses_rust_whitespace_collapse_with_fallback(self) -> None:
        text = "<ObjectName>\x1cExample\u2003 Item \t\x1f</ObjectName>"
        expected = "Example Item"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            self.assertEqual(inventory_module._first_xml_text(text, "ObjectName"), expected)
            self.assertEqual(inventory_module._first_xml_text(text, "Missing"), "")

        calls: list[str] = []
        native_collapse = native_acceleration.collapse_ascii_whitespace

        def track_collapse(value):
            calls.append(value)
            return native_collapse(value)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inventory_module,
                "_native_collapse_ascii_whitespace",
                side_effect=track_collapse,
            ):
                self.assertEqual(
                    inventory_module._first_xml_text(text, "ObjectName"), expected
                )
        self.assertEqual(calls, ["\x1cExample\u2003 Item \t\x1f"])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inventory_module,
                "_native_collapse_ascii_whitespace",
                return_value=None,
            ):
                self.assertEqual(
                    inventory_module._first_xml_text(text, "ObjectName"), expected
                )

    def test_script_reference_rewrite_uses_native_casefold_keys(self) -> None:
        new_guid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        inventory = {
            "scripts": [
                {"guid": new_guid, "object_name": "SUB_Demo", "folder": "Demo"}
            ]
        }
        payloads = [
            (
                b"<Reference><Guid>AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA</Guid>"
                b"<TypeId>sCrIpT</TypeId><ObjectName>sub_demo</ObjectName></Reference>"
            ),
            (
                b"<Reference><Guid>BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB</Guid>"
                b"<TypeId>Script</TypeId><ObjectName>SUB_Demo</ObjectName></Reference>"
            ),
        ]

        def rewrite_all():
            return [
                rewrite_script_reference_guids(
                    payload, inventory, type_ids={"sCrIpT"}
                )
                for payload in payloads
            ]

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = rewrite_all()
        self.assertEqual(len(disabled[0][1]), 1)
        self.assertEqual(disabled[0][1][0]["to_guid"], new_guid)
        self.assertEqual(disabled[0][1][0]["folder"], "Demo")
        self.assertEqual(disabled[1], (payloads[1], []))

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inventory_module,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                enabled = rewrite_all()
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(enabled, disabled)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inventory_module,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback = rewrite_all()
        self.assertEqual(fallback, disabled)

    def test_inventory_unique_collision_and_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fc_inv_") as tmp:
            root = Path(tmp)
            _write_xscr(root, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "Demo", "Demo scripts")
            _write_xscr(root, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "Demo", "Demo")
            _write_xscr(root, "cccccccc-cccc-cccc-cccc-cccccccccccc", "Clash", "Demo")
            _write_xscr(root, "dddddddd-dddd-dddd-dddd-dddddddddddd", "Clash", "Demo")

            inv = build_scripts_inventory(root)
            self.assertEqual(inv["script_count"], 4)
            self.assertEqual(
                find_unique_guid(inv, "Demo", "Demo scripts"),
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            )
            self.assertIsNone(find_unique_guid(inv, "Clash", "Demo"))
            self.assertEqual(collision_preflight(inv, "Clash", "Demo")["status"], "collision")
            self.assertEqual(collision_preflight(inv, "Missing", "Demo")["status"], "missing")

            out = root / "scripts_inventory.json"
            write_scripts_inventory(out, inv)
            loaded = load_scripts_inventory(out)
            self.assertEqual(loaded["script_count"], 4)

    def test_rewrite_script_reference_guid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fc_rw_") as tmp:
            root = Path(tmp)
            _write_xscr(root, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "SUB_Demo", "Demo")
            inv = build_scripts_inventory(root)
            payload = (
                b"<Payload>\n"
                b"    <Reference>\n"
                b"      <Guid>aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa</Guid>\n"
                b"      <TypeId>Script</TypeId>\n"
                b"      <ObjectName>SUB_Demo</ObjectName>\n"
                b"    </Reference>\n"
                b"</Payload>\n"
            )
            rewritten, rewrites = rewrite_script_reference_guids(payload, inv)
            self.assertEqual(len(rewrites), 1)
            self.assertEqual(rewrites[0]["to_guid"], "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
            self.assertIn(b"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", rewritten)
            self.assertNotIn(b"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", rewritten)

    def test_explicit_target_profile_isolation_and_unbound_prereq(self) -> None:
        target_a = {
            "fingerprint": "target-a",
            "objects": {
                "userspecific": [
                    {
                        "guid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "object_name": "SUB_Demo",
                        "object_subfolder_path": "Target A",
                        "kind": "user_specific",
                    }
                ],
                "systemspecific": [],
            },
        }
        target_b = {
            "fingerprint": "target-b",
            "objects": {
                "userspecific": [
                    {
                        "guid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                        "object_name": "SUB_Demo",
                        "object_subfolder_path": "Target B",
                        "kind": "user_specific",
                    }
                ],
                "systemspecific": [],
            },
        }
        inventory_a = build_scripts_inventory_from_target_profile(target_a)
        inventory_b = build_scripts_inventory_from_target_profile(target_b)
        self.assertEqual(
            find_unique_guid(inventory_a, "SUB_Demo", "Target A"),
            target_a["objects"]["userspecific"][0]["guid"],
        )
        self.assertIsNone(find_unique_guid(inventory_a, "SUB_Demo", "Target B"))
        self.assertEqual(inventory_b["target_profile_fingerprint"], "target-b")
        reference = (
            b"<Reference><Guid>cccccccc-cccc-cccc-cccc-cccccccccccc</Guid>"
            b"<TypeId>Script</TypeId><ObjectName>SUB_Demo</ObjectName></Reference>"
        )
        rewritten_a, _ = rewrite_script_reference_guids(reference, inventory_a)
        rewritten_b, _ = rewrite_script_reference_guids(reference, inventory_b)
        self.assertIn(target_a["objects"]["userspecific"][0]["guid"].encode(), rewritten_a)
        self.assertIn(target_b["objects"]["userspecific"][0]["guid"].encode(), rewritten_b)
        self.assertNotEqual(rewritten_a, rewritten_b)

        payload = (
            b"<Reference><Guid>cccccccc-cccc-cccc-cccc-cccccccccccc</Guid>"
            b"<TypeId>WorktableWorkspace</TypeId><ObjectName>Unknown_WT</ObjectName></Reference>"
        )
        report = report_missing_system_dependencies(payload, use_local_defaults=False)
        self.assertTrue(report["target_unbound"])
        self.assertEqual(report["missing_count"], 0)
        self.assertEqual(report["review"][0]["reason"], "target_profile_not_selected")

    def test_missing_system_dependency_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fc_sys_") as tmp:
            sys_root = Path(tmp) / "SystemSpecific"
            wsp = sys_root / "Worktable" / "Workspaces"
            wsp.mkdir(parents=True)
            guid = "11111111-1111-1111-1111-111111111111"
            (wsp / f"{guid}.xwsp").write_text(
                (
                    '<?xml version="1.0"?>\n'
                    "<VxData><Payload>\n"
                    "  <ObjectName>Known_WT</ObjectName>\n"
                    "</Payload></VxData>\n"
                ),
                encoding="utf-8",
            )
            payload = (
                b"<Reference>\n"
                b"      <Guid>22222222-2222-2222-2222-222222222222</Guid>\n"
                b"      <TypeId>WorktableWorkspace</TypeId>\n"
                b"      <ObjectName>Missing_WT</ObjectName>\n"
                b"    </Reference>\n"
                b"<Reference>\n"
                b"      <Guid>11111111-1111-1111-1111-111111111111</Guid>\n"
                b"      <TypeId>WorktableWorkspace</TypeId>\n"
                b"      <ObjectName>Known_WT</ObjectName>\n"
                b"    </Reference>\n"
            )
            report = report_missing_system_dependencies(
                payload,
                systemspecific_dir=sys_root,
            )
            self.assertEqual(report["missing_count"], 1)
            self.assertEqual(report["missing"][0]["object_name"], "Missing_WT")


if __name__ == "__main__":
    unittest.main()
