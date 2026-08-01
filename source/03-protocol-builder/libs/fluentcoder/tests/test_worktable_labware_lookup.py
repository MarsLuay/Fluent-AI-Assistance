"""Worktable labware lookup ergonomics: labware(), __getitem__, has_labware."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import Plate96, Worktable  # noqa: E402


@pytest.mark.usefixtures("synthetic_catalog")
def test_labware_alias_and_getitem_find_placed_labware() -> None:
    wt = Worktable(name="Lookup test")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dest = wt.place(Plate96("DestPlate", catalog="96 Well Flat"), "Nest", 2)

    assert wt.labware("Source") is src
    assert wt["DestPlate"] is dest
    assert wt.labware_by_label("Source") is src


@pytest.mark.usefixtures("synthetic_catalog")
def test_has_labware() -> None:
    wt = Worktable(name="Has labware test")
    wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)

    assert wt.has_labware("Source") is True
    assert wt.has_labware("DestPlate") is False


@pytest.mark.usefixtures("synthetic_catalog")
def test_labware_by_label_keyerror_lists_available_labels() -> None:
    wt = Worktable(name="KeyError test")
    wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    wt.place(Plate96("DestPlate", catalog="96 Well Flat"), "Nest", 2)

    with pytest.raises(KeyError, match=r"Available labels: \['DestPlate', 'Source'\]"):
        wt.labware("Missing")


@pytest.mark.usefixtures("synthetic_catalog")
def test_labware_by_label_keyerror_empty_deck() -> None:
    wt = Worktable(name="Empty deck test")

    with pytest.raises(KeyError, match="deck is empty"):
        wt["Missing"]

