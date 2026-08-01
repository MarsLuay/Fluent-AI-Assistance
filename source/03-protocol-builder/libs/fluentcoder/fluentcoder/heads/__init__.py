"""Pipetting heads — MCA-96, MCA-384, FCA, LiHa.

MCA-96 and MCA-384 use dedicated IR step families. FCA and LiHa share the
LiHa IR step types; ``FCAHead`` (``wt.fca``) is an ergonomic facade with
FCA tip semantics and compile-aligned liquid-class defaults, while ``LiHa``
(``wt.liha``) exposes the same steps with explicit parameters.
"""

from .mca96 import MCA96Head, Tip
from .mca384 import MCA384Head
from .liha import LiHa
from .fca import FCAHead

__all__ = ["MCA96Head", "MCA384Head", "Tip", "LiHa", "FCAHead"]
