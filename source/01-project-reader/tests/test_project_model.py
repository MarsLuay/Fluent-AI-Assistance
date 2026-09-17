from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tecan_reader.project_model import (
    CANONICAL_PROJECT_MODEL_SCHEMA_VERSION,
    CanonicalProjectModel,
    SourceProvenance,
    normalize_record,
)


class ProjectModelTests(unittest.TestCase):
    def test_provenance_is_json_serializable_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record = normalize_record(
                {
                    "source": "Scripts/main.xscr",
                    "object_name": "Main",
                    "checksum": "abc",
                    "source_metadata": {"unknown_fields": {"NewField": ["value"]}},
                },
                kind="script",
                source_archive=Path(tmp) / "input.zeia",
            )
            model = CanonicalProjectModel(
                source_archive=str((Path(tmp) / "input.zeia").resolve()),
                adapter_id="test",
                detection={"status": "supported"},
                scripts=[record],
                objects=[],
                worklists=[],
                errors=[],
                source_metadata={"entry_count": 1, "extension_counts": {".xscr": 1}},
            )

            first = model.to_json()
            second = model.to_json()
            payload = json.loads(first)

        self.assertEqual(first, second)
        self.assertEqual(payload["schema_version"], CANONICAL_PROJECT_MODEL_SCHEMA_VERSION)
        self.assertEqual(payload["scripts"][0]["provenance"]["entry_path"], "Scripts/main.xscr")
        self.assertEqual(
            payload["scripts"][0]["provenance"]["original_identifiers"]["object_name"],
            "Main",
        )
        self.assertEqual(
            payload["scripts"][0]["source_metadata"]["unknown_fields"]["NewField"],
            ["value"],
        )

    def test_source_provenance_shape(self) -> None:
        provenance = SourceProvenance(
            source_archive="input.zeia",
            entry_path="Scripts/main.xscr",
            original_identifiers={"guid": "abc", "object_name": "Main"},
        )
        self.assertEqual(
            provenance.to_dict(),
            {
                "source_archive": "input.zeia",
                "entry_path": "Scripts/main.xscr",
                "original_identifiers": {"guid": "abc", "object_name": "Main"},
            },
        )


if __name__ == "__main__":
    unittest.main()
