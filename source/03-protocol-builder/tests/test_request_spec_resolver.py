import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.request_spec import write_request_spec
from fluent_pipeline.request_spec_resolver import (
    bundle_dir_for_request_spec,
    enumerate_request_spec_candidates,
    enumerate_ready_bundle_dirs_for_stem,
    normalize_protocol_stem,
    resolve_latest_request_spec,
    resolve_request_spec_path,
    ready_to_import_script_names,
)


def _minimal_spec(protocol_name: str) -> dict:
    return {
        "schema_version": "tecan.request_spec.v1",
        "request": {
            "intent": "test",
            "protocol_name": protocol_name,
            "status": "reviewed",
        },
        "source": {},
        "generation": {},
        "review": {},
        "acceptance": {},
    }


class RequestSpecResolverTests(unittest.TestCase):
    def test_normalize_protocol_stem(self):
        self.assertEqual(
            normalize_protocol_stem("Verification_Script1"),
            "verification_script1",
        )
        self.assertEqual(
            normalize_protocol_stem("verification-script1-v3"),
            "verification_script1_v3",
        )

    def test_resolve_latest_prefers_highest_version_and_ready_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_root = root / "ready-to-import"
            build_root = root / "build" / "generations"
            projects_root = root / "projects"

            old_bundle = ready_root / "demo_protocol_v2" / "source"
            new_bundle = ready_root / "demo_protocol_v5" / "source"
            stale_generation = build_root / "demo-protocol-v1"
            for directory in (old_bundle, new_bundle, stale_generation):
                directory.mkdir(parents=True)
            write_request_spec(_minimal_spec("Demo_Protocol"), old_bundle / "request.spec.yaml")
            write_request_spec(_minimal_spec("Demo_Protocol"), new_bundle / "request.spec.yaml")
            write_request_spec(_minimal_spec("Demo_Protocol"), stale_generation / "request.spec.yaml")

            candidates = enumerate_request_spec_candidates(
                projects_dir=projects_root,
                ready_to_import_dir=ready_root,
                build_generations_dir=build_root,
            )
            latest = resolve_latest_request_spec(
                protocol_name="Demo_Protocol",
                candidates=candidates,
            )
            self.assertEqual(latest, (new_bundle / "request.spec.yaml").resolve())

    def test_versioned_generation_path_auto_upgrades_unless_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_root = root / "ready-to-import"
            build_root = root / "build" / "generations"
            projects_root = root / "projects"
            stale = build_root / "demo-protocol-v1"
            latest_bundle = ready_root / "demo_protocol_v4" / "source"
            stale.mkdir(parents=True)
            latest_bundle.mkdir(parents=True)
            write_request_spec(_minimal_spec("Demo_Protocol"), stale / "request.spec.yaml")
            write_request_spec(_minimal_spec("Demo_Protocol"), latest_bundle / "request.spec.yaml")
            candidates = enumerate_request_spec_candidates(
                projects_dir=projects_root,
                ready_to_import_dir=ready_root,
                build_generations_dir=build_root,
            )

            upgraded, info = resolve_request_spec_path(
                stale / "request.spec.yaml",
                candidates=candidates,
            )
            self.assertTrue(info["upgraded"])
            self.assertEqual(upgraded, (latest_bundle / "request.spec.yaml").resolve())

            pinned, pin_info = resolve_request_spec_path(
                stale / "request.spec.yaml",
                pin=True,
                candidates=candidates,
            )
            self.assertFalse(pin_info["upgraded"])
            self.assertEqual(pinned, (stale / "request.spec.yaml").resolve())

    def test_latest_alias_resolves_newest_matching_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_root = root / "ready-to-import"
            build_root = root / "build" / "generations"
            projects_root = root / "projects"
            bundle = ready_root / "mars_script_v3" / "source"
            bundle.mkdir(parents=True)
            write_request_spec(_minimal_spec("Mars_Script"), bundle / "request.spec.yaml")
            candidates = enumerate_request_spec_candidates(
                projects_dir=projects_root,
                ready_to_import_dir=ready_root,
                build_generations_dir=build_root,
            )
            resolved, info = resolve_request_spec_path("latest:Mars_Script", candidates=candidates)
            self.assertEqual(info["reason"], "latest_alias")
            self.assertEqual(resolved, (bundle / "request.spec.yaml").resolve())

    def test_ready_to_import_script_names_reads_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ready-to-import"
            bundle = root / "demo_protocol_v7" / "source"
            bundle.mkdir(parents=True)
            metadata = {
                "bundle_name": "demo_protocol_v7",
                "script_name": "demo_protocol",
                "script_naming": {
                    "final_name": "Demo_Protocol_v7",
                    "requested_name": "Demo_Protocol",
                    "base_name": "Demo_Protocol",
                },
            }
            (bundle / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            names = ready_to_import_script_names(root)
            self.assertIn("Demo_Protocol_v7", names)
            self.assertIn("Demo_Protocol", names)
            self.assertIn("demo_protocol_v7", names)

    def test_bundle_dir_for_request_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "ready-to-import" / "demo_protocol_v3" / "source"
            bundle.mkdir(parents=True)
            spec_path = bundle / "request.spec.yaml"
            spec_path.write_text("request: {}\n", encoding="utf-8")
            self.assertEqual(
                bundle_dir_for_request_spec(spec_path),
                (Path(tmp) / "ready-to-import" / "demo_protocol_v3").resolve(),
            )

    def test_latest_alias_attaches_source_bundle_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_root = root / "ready-to-import"
            build_root = root / "build" / "generations"
            projects_root = root / "projects"
            bundle = ready_root / "mars_script_v3" / "source"
            bundle.mkdir(parents=True)
            write_request_spec(_minimal_spec("Mars_Script"), bundle / "request.spec.yaml")
            candidates = enumerate_request_spec_candidates(
                projects_dir=projects_root,
                ready_to_import_dir=ready_root,
                build_generations_dir=build_root,
            )
            _, info = resolve_request_spec_path("latest:Mars_Script", candidates=candidates)
            self.assertEqual(
                info["source_bundle_dir"],
                str((ready_root / "mars_script_v3").resolve()),
            )

    def test_pin_spec_omits_source_bundle_dir_for_versioned_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_root = root / "ready-to-import"
            build_root = root / "build" / "generations"
            projects_root = root / "projects"
            stale = build_root / "demo-protocol-v1"
            latest_bundle = ready_root / "demo_protocol_v4" / "source"
            stale.mkdir(parents=True)
            latest_bundle.mkdir(parents=True)
            write_request_spec(_minimal_spec("Demo_Protocol"), stale / "request.spec.yaml")
            write_request_spec(_minimal_spec("Demo_Protocol"), latest_bundle / "request.spec.yaml")
            candidates = enumerate_request_spec_candidates(
                projects_dir=projects_root,
                ready_to_import_dir=ready_root,
                build_generations_dir=build_root,
            )
            _, info = resolve_request_spec_path(
                stale / "request.spec.yaml",
                pin=True,
                candidates=candidates,
            )
            self.assertTrue(info["pinned"])
            self.assertNotIn("source_bundle_dir", info)

    def test_enumerate_ready_bundle_dirs_for_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready_root = Path(tmp) / "ready-to-import"
            for name in ("demo_protocol_v2", "demo_protocol_v5", "other_script_v9"):
                (ready_root / name).mkdir(parents=True)
            bundles = enumerate_ready_bundle_dirs_for_stem(
                "Demo_Protocol",
                ready_to_import_dir=ready_root,
            )
            self.assertEqual(
                [path.name for path in bundles],
                ["demo_protocol_v5", "demo_protocol_v2"],
            )

    def test_ignores_legacy_project_generation_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects_root = root / "projects"
            legacy = projects_root / "legacy" / "build" / "generations" / "demo_v1"
            legacy.mkdir(parents=True)
            write_request_spec(_minimal_spec("Demo"), legacy / "request.spec.yaml")

            candidates = enumerate_request_spec_candidates(
                projects_dir=projects_root,
                ready_to_import_dir=root / "ready-to-import",
                build_generations_dir=root / "build" / "generations",
            )

            self.assertEqual(candidates, [])

    def test_enumerates_project_temp_generation_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_root = root / "ready-to-import"
            generation = ready_root / "demo" / "temp_files" / "build" / "generations" / "demo_v2"
            generation.mkdir(parents=True)
            write_request_spec(_minimal_spec("Demo"), generation / "request.spec.yaml")

            candidates = enumerate_request_spec_candidates(
                ready_to_import_dir=ready_root,
                build_generations_dir=ready_root / "unscoped" / "temp_files" / "build" / "generations",
                project_temp_root=ready_root,
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].source, "project-temp-generations")
            self.assertEqual(candidates[0].path, (generation / "request.spec.yaml").resolve())


if __name__ == "__main__":
    unittest.main()
