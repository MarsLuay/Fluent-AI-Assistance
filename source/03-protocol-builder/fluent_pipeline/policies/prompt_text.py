"""Shared prompt-text policy for media markers and placeholder detection."""

from __future__ import annotations

import re

MEDIA_PLACEHOLDER_BEGIN = "[Reference media:"
MEDIA_PLACEHOLDER_END = "]"

PROMPT_PLACEHOLDER_TOKENS = frozenset(
    {
        "todo",
        "tbd",
        "tba",
        "tbc",
        "fixme",
        "placeholder",
        "fill in",
        "fill me in",
        "fill this in",
        "xxx",
        "n/a",
        "na",
        "prompt",
        "prompt text",
        "text",
        "text here",
        "enter text",
        "edit me",
        "replace me",
        "insert text",
        "lorem ipsum",
        "...",
        "..",
        "??",
        "???",
    }
)

_MEDIA_BOILERPLATE_INLINE = (
    re.compile(
        r"\s*Reference images and videos for this prompt will be attached later\.?\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*Watch the operator reference media when it is attached,?\s*then\s+(?:confirm\s+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*Operator reference media (?:will be|to be) attached later\.?\s*",
        re.IGNORECASE,
    ),
)

__all__ = [
    "MEDIA_PLACEHOLDER_BEGIN",
    "MEDIA_PLACEHOLDER_END",
    "PROMPT_PLACEHOLDER_TOKENS",
    "normalize_operator_prompt_text",
    "prompt_has_media_boilerplate",
    "prompt_text_is_placeholder",
    "strip_media_placeholder",
]


def strip_media_placeholder(text: str) -> str:
    """Remove the trailing operator-media marker block from prompt text."""
    prompt = str(text or "")
    idx = prompt.find(MEDIA_PLACEHOLDER_BEGIN)
    if idx == -1:
        return prompt
    return prompt[:idx].rstrip()


def prompt_has_media_boilerplate(text: str) -> bool:
    """Return true when prompt text carries operator-media boilerplate."""
    prompt = str(text or "")
    if MEDIA_PLACEHOLDER_BEGIN in prompt:
        return True
    return any(pattern.search(prompt) for pattern in _MEDIA_BOILERPLATE_INLINE)


def normalize_operator_prompt_text(text: str) -> str:
    """Canonicalize prompt text for comparison, rendering, and validation."""
    prompt = strip_media_placeholder(text)
    for pattern in _MEDIA_BOILERPLATE_INLINE:
        prompt = pattern.sub(" ", prompt)
    prompt = re.sub(r"[ \t]+\n", "\n", prompt)
    prompt = re.sub(r"\n{3,}", "\n\n", prompt)
    prompt = re.sub(r"[ \t]{2,}", " ", prompt)
    return prompt.strip()


def prompt_text_is_placeholder(normalized: str) -> bool:
    """Return true when normalized prompt text looks like a placeholder."""
    prompt = str(normalized or "").strip()
    if not prompt:
        return False
    low = prompt.casefold()
    if low in PROMPT_PLACEHOLDER_TOKENS:
        return True
    if re.fullmatch(r"[\W_]+", prompt):
        return True
    bracketed = re.fullmatch(r"[<\[(]\s*(.*?)\s*[>\])]", prompt)
    if bracketed:
        inside = bracketed.group(1).casefold().strip()
        if not inside or inside in PROMPT_PLACEHOLDER_TOKENS:
            return True
        if any(keyword in inside for keyword in ("todo", "placeholder", "fill", "insert", "tbd")):
            return True
    return False

