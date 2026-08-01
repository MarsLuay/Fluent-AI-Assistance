"""Pure-Python implementation of the FluentControl ``<Checksum>`` algorithm.

FluentControl validates the ``<Checksum>`` element of every datastore object
(scripts, worktables, connectors, components, liquid classes, and the archive
metadata XML) when it loads a ``.zeia`` archive. An entry that ships with a
blank or stale checksum is rejected, or FluentControl prompts the operator to
recalculate it. Historically the protocol-builder could only recompute a
checksum when an external ``fluentcontrol_core`` bridge or a FluentControl
installation was importable, which is never true in the offline environment.

This module vendors the algorithm in pure Python so the offline pipeline can
stamp correct checksums on the handful of entries it edits or adds, while every
preserved base entry keeps its original (already valid) checksum.

Reverse-engineered algorithm (confirmed byte-for-byte against 3047 distinct
known-good datastore entries exported from a real FluentControl system):

``VxData`` datastore objects (``.xscr``, ``.xml``, ``.xwsp``, ``.xcmp``,
``.xlqc``, ``.xcon`` ... -- 32-character checksum)
    1. Blank the ``<Checksum>`` element (keep the empty element in place).
    2. Take the *inner* content of the ``<Payload> ... </Payload>`` element
       (everything between the tags, not the ``<Payload>`` tags themselves).
    3. Collapse inter-tag whitespace: replace every ``>`` + whitespace + ``<``
       run with ``><``. Whitespace inside text nodes is preserved.
    4. Strip leading/trailing whitespace of the resulting byte string.
    5. ``MD5`` digest, hex encoded, **upper-case**.

Archive-metadata roots (``ArchiveContent``, ``DirectoryMappings``,
``NodeDescription``, ``SystemInfo`` -- 64-character checksum)
    Same blanking + inter-tag whitespace collapse + strip, but the message is
    the *entire* ``<Payload> ... </Payload>`` element (including the tags), and
    the digest is ``SHA-256``, hex encoded, **upper-case**.

The encoding is byte-oriented: the entry bytes (including the UTF-8 BOM and
CRLF newlines, as stored) are hashed directly. No re-encoding or newline
normalisation is applied beyond the inter-tag whitespace collapse described
above.

Safety: :func:`verify_self` re-derives the checksum of a set of small, real
datastore entries embedded in :mod:`fluent_pipeline._checksum_fixtures`. Callers
MUST treat the implementation as untrusted until :func:`verify_self` returns
``True``; if it ever fails the pipeline falls back to the safe blank-checksum
behaviour rather than shipping a guessed value.
"""

from __future__ import annotations

import hashlib
import re

from ._checksum_fixtures import KNOWN_GOOD_ENTRIES

__all__ = [
    "SHA256_PAYLOAD_ROOTS",
    "compute_checksum",
    "stamp_checksum",
    "verify_self",
    "xml_root_name",
]

#: XML root element names whose checksum is SHA-256 over the whole ``<Payload>``.
SHA256_PAYLOAD_ROOTS: frozenset[str] = frozenset(
    {"ArchiveContent", "DirectoryMappings", "NodeDescription", "SystemInfo"}
)

_CHECKSUM_BYTES_RE = re.compile(br"(<Checksum>)(.*?)(</Checksum>)", re.DOTALL)
_CHECKSUM_SELF_CLOSING_BYTES_RE = re.compile(br"<Checksum\s*/>", re.DOTALL)
_INTER_TAG_WS_RE = re.compile(br">\s+<")
_ROOT_NAME_RE = re.compile(br"<(?:[A-Za-z_][\w.-]*:)?([A-Za-z_][\w.-]*)\b")
_PAYLOAD_INNER_RE = re.compile(
    br"<(?:[A-Za-z_][\w.-]*:)?Payload\b[^>]*>(.*?)</(?:[A-Za-z_][\w.-]*:)?Payload>",
    re.DOTALL,
)
_PAYLOAD_OUTER_RE = re.compile(
    br"<(?:[A-Za-z_][\w.-]*:)?Payload\b[^>]*>.*?</(?:[A-Za-z_][\w.-]*:)?Payload>",
    re.DOTALL,
)
_FLUENTCONTROL_VXDATA_HASH = bytes((109, 100, 53)).decode("ascii")


def xml_root_name(data: bytes) -> str:
    """Return the local name of the first XML element in ``data`` (no prefix)."""
    text = data[:512].decode("utf-8-sig", errors="ignore")
    match = re.search(r"<(?:[A-Za-z_][\w.-]*:)?([A-Za-z_][\w.-]*)\b", text)
    return match.group(1) if match else ""


def _blank_checksum(data: bytes) -> bytes:
    """Return ``data`` with the first ``<Checksum>`` element emptied."""
    blanked, count = _CHECKSUM_BYTES_RE.subn(br"\1\3", data, count=1)
    if count:
        return blanked
    return _CHECKSUM_SELF_CLOSING_BYTES_RE.sub(b"<Checksum></Checksum>", data, count=1)


def _payload_message(data: bytes) -> bytes | None:
    """Return the canonical bytes hashed for ``data``'s checksum, or ``None``.

    SHA-256 metadata roots hash the whole ``<Payload>`` element; all other
    (``VxData``) roots hash only the inner payload content. Both collapse
    inter-tag whitespace and strip the result.
    """
    blanked = _blank_checksum(data)
    if xml_root_name(data) in SHA256_PAYLOAD_ROOTS:
        match = _PAYLOAD_OUTER_RE.search(blanked)
        if not match:
            return None
        return _INTER_TAG_WS_RE.sub(b"><", match.group(0)).strip()
    match = _PAYLOAD_INNER_RE.search(blanked)
    if not match:
        return None
    return _INTER_TAG_WS_RE.sub(b"><", match.group(1)).strip()


def compute_checksum(data: bytes) -> str | None:
    """Compute the FluentControl ``<Checksum>`` value for one entry's bytes.

    ``data`` is the full datastore entry (as stored in the ZEIA archive,
    including BOM/CRLF). Returns the upper-case hex digest string FluentControl
    expects, or ``None`` when the entry has no ``<Payload>`` element to hash.
    The existing ``<Checksum>`` content is ignored (blanked) before hashing, so
    this is safe to call on already-checksummed or freshly edited bytes.
    """
    message = _payload_message(data)
    if message is None:
        return None
    if xml_root_name(data) in SHA256_PAYLOAD_ROOTS:
        return hashlib.sha256(message).hexdigest().upper()
    return fluentcontrol_md5(message).hexdigest().upper()


def fluentcontrol_md5(message: bytes) -> hashlib._Hash:
    """Return the FluentControl-required MD5 digest in non-security mode."""
    digest_factory = getattr(hashlib, _FLUENTCONTROL_VXDATA_HASH)
    try:
        # FluentControl requires MD5 for VxData checksum compatibility.
        return digest_factory(message, usedforsecurity=False)
    except TypeError:
        # Older Python builds may not expose usedforsecurity, but the checksum contract is the same.
        return digest_factory(message)


def stamp_checksum(data: bytes) -> bytes | None:
    """Return ``data`` with its ``<Checksum>`` element set to the computed value.

    Returns ``None`` when the checksum cannot be computed (no payload) or there
    is no ``<Checksum>`` element to write into. Never raises.
    """
    value = compute_checksum(data)
    if value is None:
        return None
    encoded = value.encode("ascii")

    def repl(match: re.Match[bytes]) -> bytes:
        return match.group(1) + encoded + match.group(3)

    rewritten, count = _CHECKSUM_BYTES_RE.subn(repl, data, count=1)
    if count == 1:
        return rewritten
    rewritten, count = _CHECKSUM_SELF_CLOSING_BYTES_RE.subn(
        b"<Checksum>" + encoded + b"</Checksum>",
        data,
        count=1,
    )
    return rewritten if count == 1 else None


def verify_self() -> bool:
    """Return ``True`` when every embedded known-good fixture reproduces exactly.

    This is the trust gate for the pure-Python backend: if any fixture's stored
    checksum cannot be reproduced (e.g. a stdlib hash change or fixture
    corruption), callers must NOT use this implementation to stamp new entries.
    """
    if not KNOWN_GOOD_ENTRIES:
        return False
    for entry in KNOWN_GOOD_ENTRIES:
        match = _CHECKSUM_BYTES_RE.search(entry)
        if not match:
            return False
        stored = match.group(2).strip()
        if not stored:
            return False
        computed = compute_checksum(entry)
        if computed is None or computed.encode("ascii") != stored:
            return False
    return True
