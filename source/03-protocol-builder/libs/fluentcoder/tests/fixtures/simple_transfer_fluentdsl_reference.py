"""Historical fluentdsl reference for the simple_transfer parity example.

Mirrors `examples/simple_transfer.py` exactly — same labware types, labels,
locations, positions, and pipetting volumes. The offline parity test now
compares fluentcoder output against the pinned golden fixture
``simple_transfer_expected.xscr`` instead of compiling this script.

Note: this version does not use `var("PlateType", ...)` because fluentcoder v1
does not model FC variables on the authoring side.
"""

from fluentdsl import *

protocol("Simple transfer", "Move liquid from one plate to another")
group("Setup")
add("96 Well Flat", "SourcePlate", "Site", 1)
add("96 Well Flat", "DestPlate", "Site", 2)
add("MCA96, 100ul, Box", "Tips", "Site", 4)
group("Transfer")
mca96_adapter()
mca96_tips("Tips")
mca96_aspirate("SourcePlate", 20.0)
mca96_dispense("DestPlate", 20.0)
mca96_return_tips("Tips")
mca96_drop_adapter()
