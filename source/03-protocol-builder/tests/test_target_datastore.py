"""Deterministic, explicit-target tests for ``tecan.target_datastore.v1``."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fluent_pipeline.target_datastore import (
    TARGET_DATASTORE_SCHEMA_VERSION,
    build_target_datastore_profile,
    fingerprint_target_datastore_profile,
    load_target_datastore_profile,
    write_target_datastore_profile,
)


def _write_object(root: Path, suffix: str, guid: str, name: str, folder: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{guid}{suffix}").write_text(
        "<VxData><Payload>"
        f"<ObjectName>{name}</ObjectName>"
        f"<ObjectSubfolderPath>{folder}</ObjectSubfolderPath>"
        "</Payload></VxData>\n",
        encoding="utf-8",
    )


class TargetDatastoreProfileTests(unittest.TestCase):
    def test_no_target_is_unbound_and_does_not_consult_local_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"TECAN_VISIONX_USERSPECIFIC": tmp, "TECAN_VISIONX_SYSTEMSPECIFIC": tmp},
        ):
            _write_object(Path(tmp), ".xscr", "a" * 36, "LocalOnly")
            profile = build_target_datastore_profile()

        self.assertEqual(profile["schema_version"], TARGET_DATASTORE_SCHEMA_VERSION)
        self.assertEqual(profile["status"], "unbound")
        self.assertTrue(profile["target_unbound"])
        self.assertEqual(profile["objects"], {"userspecific": [], "systemspecific": []})
        self.assertIn("target_profile_not_provided", profile["unknowns"])

    def test_explicit_target_collects_identities_and_fingerprints_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_user = Path(first) / "UserSpecific"
            first_system = Path(first) / "SystemSpecific"
            second_user = Path(second) / "UserSpecific"
            second_system = Path(second) / "SystemSpecific"
            _write_object(first_user, ".xscr", "a" * 36, "Method", "Scripts")
            _write_object(first_system, ".xwsp", "b" * 36, "Workspace")
            _write_object(second_system, ".xwsp", "b" * 36, "Workspace")
            _write_object(second_user, ".xscr", "a" * 36, "Method", "Scripts")

            kwargs = {
                "userspecific_dir": first_user,
                "systemspecific_dir": first_system,
                "software_family": "FluentControl",
                "fluentcontrol_version": "3.8",
                "fluentcontrol_build": "3.8.16.1118",
                "instrument": {"name": "Instrument A"},
                "provenance": {"source": "fixture", "build": "3.8.16.1118"},
            }
            profile = build_target_datastore_profile(**kwargs)
            equivalent = build_target_datastore_profile(
                **{**kwargs, "userspecific_dir": second_user, "systemspecific_dir": second_system}
            )

        self.assertEqual(profile["status"], "bound")
        self.assertEqual(profile["software"]["family"], "FluentControl")
        self.assertEqual(profile["objects"]["userspecific"][0]["guid"], "a" * 36)
        self.assertEqual(profile["objects"]["systemspecific"][0]["type_id"], "worktable_workspace")
        self.assertEqual(profile["fingerprint"], equivalent["fingerprint"])
        self.assertTrue(profile["target_profile_id"].startswith("target-"))

    def test_same_name_different_guid_is_preserved_as_distinct_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_root = Path(tmp) / "UserSpecific"
            system_root = Path(tmp) / "SystemSpecific"
            _write_object(user_root, ".xscr", "a" * 36, "Method")
            _write_object(system_root, ".xwsp", "b" * 36, "Method")
            profile = build_target_datastore_profile(
                userspecific_dir=user_root,
                systemspecific_dir=system_root,
                software_family="FluentControl",
            )

        records = profile["objects"]["userspecific"] + profile["objects"]["systemspecific"]
        self.assertEqual({row["guid"] for row in records}, {"a" * 36, "b" * 36})
        self.assertNotEqual(records[0]["guid"], records[1]["guid"])
        self.assertNotEqual(records[0]["kind"], records[1]["kind"])

    def test_unknown_software_family_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = build_target_datastore_profile(userspecific_dir=Path(tmp))
        self.assertIn("software_family_unknown", profile["unknowns"])

    def test_round_trip_recomputes_fingerprint_and_rejects_wrong_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = build_target_datastore_profile(software_family="FluentControl")
            path = Path(tmp) / "target.json"
            write_target_datastore_profile(path, profile)
            loaded = load_target_datastore_profile(path)
            self.assertEqual(loaded["fingerprint"], fingerprint_target_datastore_profile(loaded))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], TARGET_DATASTORE_SCHEMA_VERSION)
            with self.assertRaises(ValueError):
                load_target_datastore_profile({"schema_version": "tecan.other.v1"})

    def test_secret_fields_are_not_persisted(self) -> None:
        profile = build_target_datastore_profile(
            software_family="FluentControl",
            instrument={"name": "A", "license_token": "secret"},
            provenance={"source": "fixture", "credential": "secret"},
        )
        self.assertNotIn("license_token", profile["instrument"])
        self.assertNotIn("credential", profile["provenance"])


if __name__ == "__main__":
    unittest.main()
