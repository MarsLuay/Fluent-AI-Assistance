import json
import unittest
from pathlib import Path

from fluent_pipeline.aliases import load_alias_maps, resolve_alias
from fluent_pipeline.protocol_ir import protocol_ir_bundle_from_zeia, protocol_ir_from_gwl, protocol_ir_from_xscr


PROTOCOL_ROOT = Path(__file__).resolve().parents[1]
CORPUS_MANIFEST = PROTOCOL_ROOT / "corpus" / "local_corpus_manifest.json"
LIQUID_HANDLING = {"aspirate", "dispense"}


class LocalCorpusTests(unittest.TestCase):
    def setUp(self):
        self.manifest = _read_json(CORPUS_MANIFEST)

    def test_manifest_assets_exist_and_counts_are_current(self):
        counts: dict[str, int] = {}
        for asset in _manifest_assets(self.manifest):
            path = PROTOCOL_ROOT / asset["path"]
            self.assertTrue(path.exists(), f"Missing corpus asset: {asset['path']}")
            suffix = path.suffix.lower()
            counts[suffix] = counts.get(suffix, 0) + 1

        self.assertEqual(counts, self.manifest["extension_counts"])

    def test_imported_sample_context_matches_manifest(self):
        source = _optional_source(self.manifest, "sample-project")
        if source is None:
            self.skipTest("No source-controlled imported ZEIA fixture is currently bundled")

        project_manifest = _read_json(PROTOCOL_ROOT / source["manifest"])
        script = project_manifest["scripts"][0]

        self.assertEqual(source["script_count"], len(project_manifest["scripts"]))
        self.assertEqual(source["command_count"], script["command_count"])
        self.assertEqual(source["command_counts"], script["command_counts"])
        self.assertEqual(source["family_counts"], script["family_counts"])
        self.assertEqual(sorted(source["labware_names"]), sorted(project_manifest["labware_names"]))
        self.assertEqual(source["liquid_classes"], project_manifest["liquid_classes"])
        self.assertEqual(source["device_aliases"], script["dependencies"]["device_aliases"])
        self.assertIn(
            {
                "kind": "catalog",
                "alias": "EVA[001]",
                "canonical": "EVA",
                "source": "manifest.catalog_alias_candidates",
            },
            source["alias_candidates"],
        )

    def test_corpus_assets_parse_into_ir(self):
        expectations = _worklist_expectations(self.manifest)
        for asset in _manifest_assets(self.manifest):
            path = PROTOCOL_ROOT / asset["path"]
            with self.subTest(asset=asset["path"]):
                if path.suffix.lower() == ".zeia":
                    bundle = protocol_ir_bundle_from_zeia(path)
                    self.assertEqual(bundle["protocol_count"], 1)
                    self.assertGreater(len(bundle["protocols"][0]["steps"]), 0)
                elif path.suffix.lower() == ".xscr":
                    ir = protocol_ir_from_xscr(path)
                    self.assertGreater(len(ir["steps"]), 0)
                    self.assertGreater(len(ir["labware"]), 0)
                elif path.suffix.lower() == ".gwl":
                    ir = protocol_ir_from_gwl(path)
                    records = ir["worklists"][0]["records"]
                    expected = expectations[path.stem]
                    self.assertEqual(
                        sum(1 for step in ir["steps"] if step["operation"] in LIQUID_HANDLING),
                        expected["liquid_handling_steps"],
                    )
                    self.assertEqual(
                        sum(1 for record in records if record["operation"] == "wash"),
                        expected["wash_records"],
                    )
                    self.assertEqual(
                        sum(1 for record in records if record["operation"] == "break"),
                        expected["break_records"],
                    )

    def test_default_alias_maps_cover_manifest_expectations(self):
        aliases = load_alias_maps()
        for expectation in self.manifest["alias_map_expectations"]:
            with self.subTest(alias=expectation["alias"], kind=expectation["kind"]):
                self.assertEqual(
                    resolve_alias(expectation["alias"], expectation["kind"], aliases),
                    expectation["canonical"],
                )


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_assets(manifest: dict) -> list[dict]:
    assets = []
    for source in manifest["sources"]:
        assets.extend(source.get("assets", []))
    return assets


def _source(manifest: dict, source_id: str) -> dict:
    source = _optional_source(manifest, source_id)
    if source is not None:
        return source
    raise AssertionError(f"Missing corpus source: {source_id}")


def _optional_source(manifest: dict, source_id: str) -> dict | None:
    for source in manifest["sources"]:
        if source["id"] == source_id:
            return source
    return None


def _worklist_expectations(manifest: dict) -> dict[str, dict]:
    expectations: dict[str, dict] = {}
    for source in manifest["sources"]:
        expectations.update(source.get("record_expectations", {}))
    return expectations


if __name__ == "__main__":
    unittest.main()
