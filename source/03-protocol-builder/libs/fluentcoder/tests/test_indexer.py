from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Avoid the package-level ensure_index() trying to rebuild against a real FC install.
os.environ.setdefault("FLUENTCODER_NO_AUTO_REBUILD", "1")
os.environ.setdefault("FLUENTCODER_FC_INSTALL", str(Path(__file__).resolve().parent / "_missing_fc_install"))

from fluentcoder.catalog.indexer import index_connector_paths, build_index
from fluentcoder.catalog.xcon import XconConnector

def _fake_xcon(path: Path) -> XconConnector:
    if "bad" in path.name:
        raise ValueError("Simulated parse error")
    return XconConnector(
        guid=f"guid-{path.stem}",
        name=f"Name-{path.stem}",
        component_guid="comp-guid",
        site_guid="site-guid",
        is_default=True,
        file_path=path,
    )

class CatalogIndexerTests(unittest.TestCase):

    def test_index_connector_paths(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            install = tmp / "install"
            components = install / "SystemSpecific" / "Worktable" / "Components"
            components.mkdir(parents=True)
            db_path = tmp / "index.db"

            # create empty catalog to initialize schema
            build_index(install_path=install, db_path=db_path)

            conns_dir = install / "SystemSpecific" / "Worktable" / "Connectors"
            conns_dir.mkdir(parents=True)

            good_xcon = conns_dir / "good.xcon"
            good_xcon.write_text("<Connector/>")

            bad_xcon = conns_dir / "bad.xcon"
            bad_xcon.write_text("<Bad/>")

            missing_xcon = conns_dir / "missing.xcon"

            # Path outside the install directory (should raise ValueError in _relative_install_path)
            external_dir = tmp / "external"
            external_dir.mkdir()
            external_xcon = external_dir / "external.xcon"
            external_xcon.write_text("<Connector/>")

            with mock.patch("fluentcoder.catalog.indexer.load_xcon", create=True, side_effect=_fake_xcon):
                # need to mock index_connector_paths local import as well just in case
                with mock.patch("fluentcoder.catalog.xcon.load_xcon", side_effect=_fake_xcon):
                    count = index_connector_paths(
                        [good_xcon, bad_xcon, missing_xcon, external_xcon],
                        install_path=install,
                        db_path=db_path
                    )

            # Only good_xcon and external_xcon should be indexed
            self.assertEqual(count, 2)

            # verify insertion in DB
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM connectors ORDER BY guid").fetchall()
            self.assertEqual(len(rows), 2)

            self.assertEqual(rows[0]["guid"], "guid-external")
            self.assertEqual(rows[0]["name"], "Name-external")
            self.assertEqual(rows[0]["component_guid"], "comp-guid")
            self.assertEqual(rows[0]["site_guid"], "site-guid")
            self.assertEqual(rows[0]["is_default"], 1)

            self.assertEqual(rows[1]["guid"], "guid-good")
            self.assertEqual(rows[1]["name"], "Name-good")
            self.assertEqual(rows[1]["component_guid"], "comp-guid")
            self.assertEqual(rows[1]["site_guid"], "site-guid")
            self.assertEqual(rows[1]["is_default"], 1)

            # verify indexed_sources
            sources = conn.execute("SELECT * FROM indexed_sources").fetchall()
            # external.xcon should NOT be in indexed_sources because it raises ValueError in _relative_install_path
            # good.xcon should be in indexed_sources
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["entity_key"], "guid-good")
            self.assertEqual(sources[0]["entity_table"], "connectors")

            conn.close()

if __name__ == "__main__":
    unittest.main()
