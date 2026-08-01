"""C001: JSON liquid-class name→GUID matching matches resolve_liquid_class_guid."""

from __future__ import annotations

import unittest

from fluent_pipeline.liquid_classes_export import resolve_liquid_class_guid
from fluentcoder.compiler.renderer import liquid_class_guid_from_catalog_entries


GUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class LiquidClassJsonMatchParityTests(unittest.TestCase):
    def test_instance_suffix_and_alias_parity(self) -> None:
        catalog = {
            "entries": [
                {
                    "name": "Water Free Single",
                    "guid": GUID,
                    "aliases": ["Water Free Single", "WFS"],
                }
            ]
        }
        entries = catalog["entries"]
        for query in (
            "Water Free Single",
            "Water Free Single [1]",
            "WFS",
            "wfs",
        ):
            self.assertEqual(
                liquid_class_guid_from_catalog_entries(query, entries),
                GUID,
                query,
            )
            self.assertEqual(resolve_liquid_class_guid(query, catalog), GUID, query)

        self.assertEqual(liquid_class_guid_from_catalog_entries("Missing LC", entries), "")
        self.assertIsNone(resolve_liquid_class_guid("Missing LC", catalog))


if __name__ == "__main__":
    unittest.main()
