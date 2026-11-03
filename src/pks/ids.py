"""Stable identifiers and hashes.

IDs are the spine of the whole pipeline: every derived artefact must be
able to walk back to a byte-for-byte original. Two rules matter.

1. ``source_id`` is assigned once, at first import, and never recomputed.
   Renaming a file, moving it, or editing its content must not change it.
2. ``document_id`` is derived from ``source_id`` deterministically, so
   re-parsing the same source overwrites the same document instead of
   piling up duplicates.

Content hashes are prefixed with the algorithm so the scheme can change
later without ambiguity.
"""

from __future__ import annotations

import hashlib
import secrets
import time

# Crockford base32: no I, L, O, U. Survives hand-copying, dictation and OCR.
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def content_hash(data: bytes) -> str:
    """``sha256:...`` for raw bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def text_hash(text: str) -> str:
    """``sha256:...`` for a string, used on quotes."""
    return content_hash(text.encode("utf-8"))


def new_source_id(source_type: str, taken: set[str] | None = None) -> str:
    """``src_<type>_<YYYYMMDD>_<6 chars>``.

    ``taken`` guards against birthday collisions inside one import batch.
    """
    taken = taken or set()
    stamp = time.strftime("%Y%m%d")
    for _ in range(1000):
        token = "".join(secrets.choice(CROCKFORD) for _ in range(6))
        candidate = f"src_{source_type}_{stamp}_{token}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise RuntimeError("could not allocate a unique source_id")


def document_id_for(source_id: str, ordinal: int = 1) -> str:
    """Derive ``doc_<source minus src_>_<nn>``.

    One source usually yields one document; archives may yield several.
    """
    if not source_id.startswith("src_"):
        raise ValueError(f"not a source_id: {source_id!r}")
    return f"doc_{source_id[4:]}_{ordinal:02d}"


def new_knowledge_id() -> str:
    """26-character ULID: time-ordered, sortable, collision-resistant."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "kn_" + "".join(reversed(out))
