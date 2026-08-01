"""Product-law: no invent defaults in compile/generation paths."""

from __future__ import annotations

import itertools
import os
import unittest
from pathlib import Path

import yaml

from fluent_pipeline.generation_workflow import _recipe_step_to_ir
from fluentcoder.compiler import RenderError
from fluentcoder.compiler.renderer import Renderer
from fluentcoder.ir.schema import (
    AddLabwareStep,
    CommentStep,
    Group,
    LihaEmptyTipsStep,
    Mca384EmptyTipsStep,
    Protocol,
)


GENERATION_YAML = (
    Path(__file__).resolve().parents[1]
    / "libs"
    / "fluentcoder"
    / "fluentcoder"
    / "_assets"
    / "config"
    / "generation.yaml"
)


def _next_factory():
    counter = itertools.count(1)

    def _next_step():
        idx = next(counter)
        return idx, f"step_{idx:03d}"

    return _next_step


class GenerationYamlNoInventTests(unittest.TestCase):
    def test_shipped_worktable_and_liquid_class_empty(self) -> None:
        cfg = yaml.safe_load(GENERATION_YAML.read_text(encoding="utf-8"))
        wt = cfg.get("worktable") or {}
        self.assertFalse(str(wt.get("guid") or "").strip())
        self.assertFalse(str(wt.get("name") or "").strip())
        self.assertNotEqual(str(wt.get("name") or ""), "780_Empty")
        self.assertNotIn("11111111-1234-aaaa-ffff-000000000222", str(wt.get("guid") or ""))

        lc = cfg.get("liquid_class") or {}
        self.assertFalse(str(lc.get("name") or "").strip())
        self.assertFalse(str(lc.get("guid") or "").strip())

        device = cfg.get("device") or {}
        self.assertFalse(str(device.get("alias") or "").strip())
        self.assertFalse(str(device.get("available_id") or "").strip())

        grounding = (cfg.get("grounding_defaults") or {}).get("labware") or {}
        for key in (
            "fca_tipbox_small",
            "fca_tipbox_large",
            "mca96_tipbox_small",
            "mca96_tipbox_medium",
            "mca96_tipbox_large",
            "magnet_plate_96",
            "waste_reservoir",
        ):
            self.assertFalse(
                str(grounding.get(key) or "").strip(),
                msg=f"grounding_defaults.labware.{key} must be empty in shipped product",
            )


class RendererNoInventTests(unittest.TestCase):
    def test_unbound_protocol_fails_closed_without_worktable_fallback(self) -> None:
        protocol = Protocol(
            name="Unbound",
            groups=[Group(name="Main", steps=[CommentStep(comment="noop")])],
        )
        with self.assertRaises(RenderError) as ctx:
            Renderer().render(protocol)
        self.assertIn("provides no worktable fallback", str(ctx.exception))

    def test_empty_tips_do_not_invent_empty_tip_liquid_class(self) -> None:
        protocol = Protocol(
            name="EmptyTips",
            worktable_guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            worktable_name="Explicit WS",
            device_alias="Instrument=1/Device=MCA384:1",
            available_id="USB:TECAN,FLUENT,TESTSERIAL/MCA384:1",
            groups=[
                Group(
                    name="Main",
                    steps=[
                        Mca384EmptyTipsStep(
                            labware_name="Tips",
                            volume=10.0,
                            liquid_class="",
                            device_alias="Instrument=1/Device=MCA384:1",
                            available_id="USB:TECAN,FLUENT,TESTSERIAL/MCA384:1",
                        ),
                        LihaEmptyTipsStep(
                            labware_name="Tips",
                            volume=10.0,
                            liquid_class="",
                            device_alias="Instrument=1/Device=LIHA:1",
                            available_id="USB:TECAN,FLUENT,TESTSERIAL/LIHA:1",
                        ),
                    ],
                )
            ],
        )
        xml = Renderer().render(protocol)
        self.assertNotIn("Empty Tip", xml)
        # LiquidClassName tags may be present but must not invent product LC.
        self.assertNotIn("<LiquidClassName>Empty Tip</LiquidClassName>", xml)

    def test_device_bearing_step_fails_closed_without_available_id(self) -> None:
        protocol = Protocol(
            name="NoDevice",
            worktable_guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            worktable_name="Explicit WS",
            groups=[
                Group(
                    name="Main",
                    steps=[
                        Mca384EmptyTipsStep(
                            labware_name="Tips",
                            volume=10.0,
                            liquid_class="",
                            device_alias="Instrument=1/Device=MCA384:1",
                            available_id="",
                        ),
                    ],
                )
            ],
        )
        with self.assertRaises(RenderError) as ctx:
            Renderer().render(protocol)
        self.assertIn("AvailableID", str(ctx.exception))

    def test_alias_as_available_id_cross_fill_rejected(self) -> None:
        protocol = Protocol(
            name="CrossFill",
            worktable_guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            worktable_name="Explicit WS",
            groups=[
                Group(
                    name="Main",
                    steps=[
                        Mca384EmptyTipsStep(
                            labware_name="Tips",
                            volume=10.0,
                            liquid_class="",
                            device_alias="Instrument=1/Device=MCA384:1",
                            # Soft invent path used to stuff DeviceAlias into AvailableID.
                            available_id="Instrument=1/Device=MCA384:1",
                        ),
                    ],
                )
            ],
        )
        with self.assertRaises(RenderError) as ctx:
            Renderer().render(protocol)
        self.assertIn("cross-fill", str(ctx.exception).casefold())


class ExactLabwareCatalogTests(unittest.TestCase):
    def tearDown(self) -> None:
        Renderer._EXACT_LABWARE_BY_CASEFOLD = None
        Renderer._EXACT_LABWARE_SOURCE = None
        os.environ.pop("FLUENTCODER_LABWARE_CATALOG", None)

    def test_fuzzy_near_miss_does_not_rewrite_without_catalog(self) -> None:
        import os

        os.environ.pop("FLUENTCODER_LABWARE_CATALOG", None)
        Renderer._EXACT_LABWARE_BY_CASEFOLD = None
        Renderer._EXACT_LABWARE_SOURCE = None
        protocol = Protocol(
            name="NearMiss",
            worktable_guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            worktable_name="Explicit WS",
            groups=[
                Group(
                    name="Setup",
                    steps=[
                        AddLabwareStep(
                            label="P1",
                            labware_type="96 Well Flatt",  # near miss — must stay
                            location="Site",
                            position=1,
                        )
                    ],
                )
            ],
        )
        # Force empty exact map
        Renderer._EXACT_LABWARE_BY_CASEFOLD = {}
        Renderer._EXACT_LABWARE_SOURCE = "<test-empty>"
        r = Renderer()
        r._normalize_labware_names(protocol)
        step = protocol.groups[0].steps[0]
        self.assertEqual(step.labware_type, "96 Well Flatt")

    def test_exact_catalog_casefold_canonicalizes(self) -> None:
        import json
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "labware_catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "name": "96 Well Flat",
                                "aliases": ["96 well flat"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.environ["FLUENTCODER_LABWARE_CATALOG"] = str(catalog)
            Renderer._EXACT_LABWARE_BY_CASEFOLD = None
            Renderer._EXACT_LABWARE_SOURCE = None
            protocol = Protocol(
                name="Exact",
                worktable_guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                worktable_name="Explicit WS",
                groups=[
                    Group(
                        name="Setup",
                        steps=[
                            AddLabwareStep(
                                label="P1",
                                labware_type="96 well flat",
                                location="Site",
                                position=1,
                            )
                        ],
                    )
                ],
            )
            Renderer()._normalize_labware_names(protocol)
            self.assertEqual(protocol.groups[0].steps[0].labware_type, "96 Well Flat")


class A200DispenseNoInventTests(unittest.TestCase):
    def _full_payload(self, **overrides):
        payload = {
            "type": "a200_dispense",
            "volume_ul": 100,
            "macro_name": "DriverRun",
            "wait_macro": "DriverWait",
            "module_name": "DriverModule",
            "execution_settings": "CUSTOM_SETTINGS,~a200startwell~,~a200endwell~,0",
            "wait_timeout": "120",
            "start_well": 1,
            "end_well": 8,
        }
        payload.update(overrides)
        return payload

    def test_full_payload_emits_vol_transfer_max_and_settings(self) -> None:
        built = _recipe_step_to_ir(self._full_payload(), "Group", _next_factory(), [])
        self.assertIsInstance(built, list)
        assert isinstance(built, list)
        ops = [step["operation"] for step in built]
        self.assertEqual(ops.count("set_variable"), 3)
        self.assertEqual(ops.count("application_driver_macro"), 2)
        vol = next(s for s in built if s["parameters"].get("variable") == "VolTransferMax")
        self.assertEqual(vol["parameters"]["value"], 100)
        run = next(s for s in built if s["name"].endswith(" Run"))
        self.assertEqual(
            run["parameters"]["execution_settings"],
            "CUSTOM_SETTINGS,~a200startwell~,~a200endwell~,0",
        )
        self.assertNotIn("SPE 4", run["parameters"]["execution_settings"])

    def test_missing_execution_settings_fails_closed(self) -> None:
        payload = self._full_payload()
        del payload["execution_settings"]
        self.assertIsNone(_recipe_step_to_ir(payload, "Group", _next_factory(), []))

    def test_missing_wait_timeout_fails_closed(self) -> None:
        payload = self._full_payload()
        del payload["wait_timeout"]
        self.assertIsNone(_recipe_step_to_ir(payload, "Group", _next_factory(), []))

    def test_missing_start_or_end_well_fails_closed(self) -> None:
        for key in ("start_well", "end_well"):
            payload = self._full_payload()
            del payload[key]
            self.assertIsNone(
                _recipe_step_to_ir(payload, "Group", _next_factory(), []),
                msg=f"expected fail closed without {key}",
            )

    def test_missing_macro_fields_still_fail_closed(self) -> None:
        for key in ("macro_name", "wait_macro", "module_name"):
            payload = self._full_payload()
            del payload[key]
            self.assertIsNone(
                _recipe_step_to_ir(payload, "Group", _next_factory(), []),
                msg=f"expected fail closed without {key}",
            )


class LihaDispenseNoInventTests(unittest.TestCase):
    def test_missing_well_fails_closed(self) -> None:
        payload = {
            "type": "liha_dispense",
            "labware": "Dest",
            "volume_ul": 10,
            "liquid_class": "Water",
            "device_alias": "Instrument=1/Device=LIHA:1",
        }
        self.assertIsNone(_recipe_step_to_ir(payload, "Group", _next_factory(), []))

    def test_explicit_well_emits(self) -> None:
        payload = {
            "type": "liha_dispense",
            "labware": "Dest",
            "volume_ul": 10,
            "liquid_class": "Water",
            "well": "B2",
            "device_alias": "Instrument=1/Device=LIHA:1",
        }
        built = _recipe_step_to_ir(payload, "Group", _next_factory(), [])
        self.assertIsInstance(built, dict)
        self.assertEqual(built["parameters"]["well"], "B2")


class EmptyTipSchemaDefaultsTests(unittest.TestCase):
    def test_empty_tips_steps_default_liquid_class_none(self) -> None:
        self.assertIsNone(Mca384EmptyTipsStep(labware_name="W", volume=1).liquid_class)
        self.assertIsNone(LihaEmptyTipsStep(labware_name="W", volume=1).liquid_class)


if __name__ == "__main__":
    unittest.main()
