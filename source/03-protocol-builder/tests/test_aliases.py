import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.aliases import (
    DEFAULT_ALIAS_DIR,
    load_alias_maps,
    load_alias_maps_with_context_catalog,
    normalize_protocol_ir_aliases,
    resolve_alias,
)


class AliasTests(unittest.TestCase):
    def test_repo_alias_yaml_has_no_falcon_or_capholder_defaults(self):
        aliases = load_alias_maps(DEFAULT_ALIAS_DIR)
        for kind in ("labware_aliases", "catalog_aliases"):
            for alias, canonical in (aliases.get(kind) or {}).items():
                blob = f"{alias} {canonical}".casefold()
                self.assertNotIn("falcon", blob, f"{kind}: {alias!r} -> {canonical!r}")
                self.assertNotIn("capholder", blob, f"{kind}: {alias!r} -> {canonical!r}")

    def test_instance_suffix_strips_without_repo_falcon_map(self):
        aliases = {"labware_aliases": {}, "catalog_aliases": {}}
        self.assertEqual(
            resolve_alias("CapHolder_long_44mm[001]", "labware", aliases),
            "CapHolder_long_44mm",
        )
        self.assertEqual(
            resolve_alias("1x10 50ml Falcon Tube Runner[002]", "catalog", aliases),
            "1x10 50ml Falcon Tube Runner",
        )

    def test_zeia_catalog_overlay_supplies_site_labware_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "labware_aliases.yaml").write_text(
                'labware_aliases:\n  "EVA[001]": "EVA"\n',
                encoding="utf-8",
            )
            (root / "catalog_aliases.yaml").write_text(
                "catalog_aliases: {}\n",
                encoding="utf-8",
            )
            catalog_path = root / "labware_catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "name": "Demo_CapHolder",
                                "aliases": ["Demo_CapHolder[001]", "Demo_CapHolder"],
                            },
                            {
                                "name": "Demo_Tube_Runner",
                                "aliases": ["Demo_Tube_Runner[001]"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            maps = load_alias_maps_with_context_catalog(
                root,
                labware_catalog_path=catalog_path,
            )
        self.assertEqual(maps["labware_aliases"]["EVA[001]"], "EVA")
        self.assertEqual(maps["catalog_aliases"]["Demo_CapHolder[001]"], "Demo_CapHolder")
        self.assertEqual(maps["labware_aliases"]["Demo_Tube_Runner[001]"], "Demo_Tube_Runner")
        self.assertNotIn("CapHolder_long_44mm[001]", maps["labware_aliases"])

    def test_load_alias_maps_and_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "labware_aliases.yaml").write_text(
                'labware_aliases:\n  "MCA384TipBox[001]": "MCA384TipBox"\n',
                encoding="utf-8",
            )

            aliases = load_alias_maps(root)

        self.assertEqual(aliases["labware_aliases"], {"MCA384TipBox[001]": "MCA384TipBox"})
        self.assertEqual(resolve_alias("MCA384TipBox[001]", "labware", aliases), "MCA384TipBox")
        self.assertEqual(resolve_alias("MCA384TipBox", "labware", aliases), "MCA384TipBox")

    def test_load_alias_maps_accepts_quoted_colon_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "device_aliases.yaml").write_text(
                'device_aliases:\n  "Instrument=1/Device=MCA384:1": "MCA384"\n',
                encoding="utf-8",
            )

            aliases = load_alias_maps(root)

        self.assertEqual(
            resolve_alias("Instrument=1/Device=MCA384:1", "device_alias", aliases),
            "MCA384",
        )

    def test_normalize_protocol_ir_aliases(self):
        aliases = {
            "catalog_aliases": {"Plexiglas Pane[002]": "Plexiglas Pane", "EVA[001]": "EVA"},
            "labware_aliases": {"MCA384TipBox[001]": "MCA384TipBox", "EVA[001]": "EVA"},
            "liquid_class_aliases": {"Water Free Single[001]": "Water Free Single"},
            "device_aliases": {"MCA384[001]": "MCA384"},
        }
        ir = {
            "labware": [
                {"label": "MCA384TipBox[001]", "catalog": "Plexiglas Pane[002]"},
                {"label": "EVA[001]", "catalog": "EVA[001]"},
            ],
            "liquid_classes": [{"name": "Water Free Single[001]"}],
            "dependencies": [{"kind": "device_alias", "name": "MCA384[001]"}],
            "steps": [
                {
                    "target_labware": "MCA384TipBox[001]",
                    "liquid_class": "Water Free Single[001]",
                    "parameters": {"device_alias": "MCA384[001]"},
                },
                {
                    "target_labware": "EVA[001]",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "EVA[001]",
                        "label": "EVA[001]",
                        "labware_type": "EVA[001]",
                        "LabwareType": "EVA[001]",
                    },
                }
            ],
        }

        normalized = normalize_protocol_ir_aliases(ir, aliases)

        self.assertEqual(normalized["labware"][0]["label"], "MCA384TipBox")
        self.assertEqual(normalized["labware"][0]["catalog"], "Plexiglas Pane")
        self.assertEqual(normalized["labware"][1]["label"], "EVA")
        self.assertEqual(normalized["labware"][1]["catalog"], "EVA")
        self.assertEqual(normalized["liquid_classes"][0]["name"], "Water Free Single")
        self.assertEqual(normalized["dependencies"][0]["name"], "MCA384")
        self.assertEqual(normalized["steps"][0]["target_labware"], "MCA384TipBox")
        self.assertEqual(normalized["steps"][0]["liquid_class"], "Water Free Single")
        self.assertEqual(normalized["steps"][0]["parameters"]["device_alias"], "MCA384")
        self.assertEqual(normalized["steps"][1]["target_labware"], "EVA")
        self.assertEqual(normalized["steps"][1]["parameters"]["catalog"], "EVA")
        self.assertEqual(normalized["steps"][1]["parameters"]["label"], "EVA")
        self.assertEqual(normalized["steps"][1]["parameters"]["labware_type"], "EVA")
        self.assertEqual(normalized["steps"][1]["parameters"]["LabwareType"], "EVA")
        self.assertEqual(ir["labware"][0]["label"], "MCA384TipBox[001]")


if __name__ == "__main__":
    unittest.main()
