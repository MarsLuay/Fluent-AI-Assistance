"""Phase C.2 — liquid-class catalog migration.

The .xlqc files in the FC install are indexed into a ``liquid_classes``
table. The renderer uses ``resolve_liquid_class_by_name`` to pick the
GUID for the .xscr's top-level ``<Reference TypeId="LiquidClass">``,
replacing the hardcoded value in ``generation.yaml``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.catalog import (  # noqa: E402
    index_exists,
    load_xlqc,
    resolve_liquid_class_by_name,
)
from fluentcoder.catalog.paths import index_db_path_default  # noqa: E402


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_liquid_classes_table_populated() -> None:
    """The indexer wrote at least one liquid_classes row."""
    import sqlite3

    conn = sqlite3.connect(str(index_db_path_default()))
    try:
        n = conn.execute("SELECT COUNT(*) FROM liquid_classes").fetchone()[0]
    finally:
        conn.close()
    assert n >= 30, (
        f"expected at least 30 liquid_classes rows after build_index, got {n}"
    )


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_water_free_single_resolves() -> None:
    """Install/ZEIA catalog resolves Water Free Single — not generation.yaml invent."""
    entry = resolve_liquid_class_by_name("Water Free Single")
    assert entry is not None, "Water Free Single should be in the index"
    assert entry.guid, "GUID must be non-empty"
    assert "Fca" in entry.supported_heads
    assert any(head.startswith("Mca") for head in entry.supported_heads)

    import yaml
    gen_yaml = REPO_ROOT / "fluentcoder" / "_assets" / "config" / "generation.yaml"
    cfg = yaml.safe_load(gen_yaml.read_text(encoding="utf-8"))
    # Shipped product yaml must not invent a liquid-class name/GUID.
    assert not str(cfg.get("liquid_class", {}).get("name") or "").strip()
    assert not str(cfg.get("liquid_class", {}).get("guid") or "").strip()

    parsed = load_xlqc(entry.file_path)
    assert "Fca" in parsed.supported_heads
    assert any(head.startswith("Mca") for head in parsed.supported_heads)


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_unknown_liquid_class_returns_none() -> None:
    assert resolve_liquid_class_by_name("This Liquid Class Does Not Exist") is None

