"""FluentControl naming helpers shared across export/alias surfaces."""

from __future__ import annotations

import re
from typing import Any

INSTANCE_SUFFIX_RE = re.compile(r"\[\s*[^\]]+\s*\]\s*$")


def strip_fluent_instance_suffix(value: Any) -> str:
    """Strip Fluent instance suffixes such as ``[001]`` or ``[platecount]``."""
    text = str(value or "").strip()
    if not text:
        return ""
    return INSTANCE_SUFFIX_RE.sub("", text).strip() or text
