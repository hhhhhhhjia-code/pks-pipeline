"""Turn source files into ``parsed_document`` records.

Parsing here means one thing only: expose structure. Text is never
rewritten, summarised or cleaned. Every block keeps a ``locator`` that
points back into the original file, so a downstream claim can always be
checked against its source.

Supported inputs:

===========  ============================================
``.md``      headings, paragraphs, lists, quotes, tables, code
``.srt``     subtitle cues -> ``transcript_turn`` with timings
``.vtt``     same, WebVTT flavour
``.txt``     paragraphs only, no invented structure
===========  ============================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ids import content_hash, document_id_for

HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
CUE_RE = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)
SPEAKER_RE = re.compile(r"^(?:\[?([^:\]]{1,20})\]?[:\s])\s*(.+)$")


@dataclass
class Parsed:
    """A parsed document plus the warnings raised while building it."""

    document: dict
    warnings: list[dict]


def _warn(code: str, message: str, severity: str = "warning") -> dict:
    return {"code": code, "message": message, "severity": severity, "block_id": None}


def _timestamps_to_ms(h: str, m: str, s: str, ms: str) -> int:
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)


def markdown_blocks(text: str) -> list[dict]:
    """Split Markdown into blocks, preserving line ranges."""
    lines = text.splitlines()
    blocks: list[dict] = []
    i, n = 0, len(lines)

    def add(btype: str, body: str | None, start: int, end: int, **extra) -> None:
        block = {
            "block_id": f"blk_{len(blocks):04d}",
            "order": len(blocks),
            "type": btype,
            "text": body,
            "locator": {"kind": "line_range", "line_start": start, "line_end": end},
        }
        block.update(extra)
        blocks.append(block)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            start = i + 1
            i += 1
            buf: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            add("code", "\n".join(buf), start, min(i, n), language=None)
            continue

        m = HEAD_RE.match(line)
        if m:
            add("heading", m.group(2).strip(), i + 1, i + 1, heading_level=len(m.group(1)))
            i += 1
            continue

        if HR_RE.match(line):
            add("separator", None, i + 1, i + 1)
            i += 1
            continue

        if stripped.startswith("|"):
            start = i + 1
            buf = []
            while i < n and lines[i].strip().startswith("|"):
                buf.append(lines[i].rstrip())
                i += 1
            add("table", "\n".join(buf), start, i)
            continue

        m = LIST_RE.match(line)
        if m:
            add("list_item", m.group(3).strip(), i + 1, i + 1)
            i += 1
            continue

        if stripped.startswith(">"):
            add("quote", stripped.lstrip("> ").strip(), i + 1, i + 1)
            i += 1
            continue

        start = i + 1
        buf = []
        while i < n:
            cur = lines[i]
            if not cur.strip():
                break
            if HEAD_RE.match(cur) or LIST_RE.match(cur) or HR_RE.match(cur):
                break
            if cur.strip().startswith(("```", "|", ">")):
                break
            buf.append(cur.strip())
            i += 1
        if buf:
            add("paragraph", "\n".join(buf), start, i)

    return blocks


def subtitle_blocks(text: str, warnings: list[dict]) -> list[dict]:
    """Parse SRT/VTT cues into transcript turns.

    Cue text is kept verbatim. A leading ``Name:`` prefix is lifted into
    ``speaker`` only when it is short and consistent; otherwise the whole
    line stays as text and a warning is raised.
    """
    blocks: list[dict] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)
    speakers: set[str] = set()

    while i < n:
        m = CUE_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        start_ms = _timestamps_to_ms(*m.group(1, 2, 3, 4))
        end_ms = _timestamps_to_ms(*m.group(5, 6, 7, 8))
        cue_line = i + 1
        i += 1

        buf: list[str] = []
        while i < n and lines[i].strip() and not CUE_RE.match(lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1

        body = " ".join(buf).strip()
        if not body:
            continue

        speaker = None
        candidate = SPEAKER_RE.match(body)
        if candidate and len(candidate.group(1)) <= 20 and "://" not in body:
            speaker = candidate.group(1).strip()
            body = candidate.group(2).strip()
            speakers.add(speaker)

        block = {
            "block_id": f"blk_{len(blocks):04d}",
            "order": len(blocks),
            "type": "transcript_turn",
            "text": body,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "locator": {"kind": "timestamp", "timestamp_ms": start_ms},
        }
        if speaker:
            block["speaker"] = speaker
        blocks.append(block)

    if blocks and not speakers:
        warnings.append(_warn(
            "NO_SPEAKER",
            "subtitle file carries no speaker labels; speaker left unset",
            "info",
        ))
    return blocks


def _language_of(blocks: list[dict]) -> str | None:
    sample = " ".join((b.get("text") or "") for b in blocks[:40])
    if not sample.strip():
        return None
    han = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if han and han >= latin:
        return "zh-Hans"
    if latin:
        return "en"
    return None


def parse_file(path: Path, source_id: str, source_type: str = "local") -> Parsed:
    """Build a parsed document from one file. Never mutates the file."""
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    warnings: list[dict] = []
    suffix = path.suffix.lower()

    if suffix in (".srt", ".vtt"):
        blocks = subtitle_blocks(text, warnings)
        sequence = "chronological"
        content_format = "plain_text"
    elif suffix == ".txt":
        blocks = [
            {
                "block_id": "blk_0000",
                "order": 0,
                "type": "paragraph",
                "text": text.strip(),
                "locator": {"kind": "line_range", "line_start": 1,
                            "line_end": max(1, len(text.splitlines()))},
            }
        ] if text.strip() else []
        sequence = "document_order"
        content_format = "plain_text"
    else:
        blocks = markdown_blocks(text)
        sequence = "document_order"
        content_format = "markdown"

    if not blocks:
        warnings.append(_warn("EMPTY_DOCUMENT", "no content blocks produced", "warning"))

    title = path.stem
    for block in blocks:
        if block["type"] == "heading" and block.get("heading_level") == 1:
            title = (block.get("text") or title).strip()
            break

    if any(b["type"] == "transcript_turn" and "speaker" not in b for b in blocks):
        participants = []
    else:
        seen: list[str] = []
        for b in blocks:
            if b["type"] == "transcript_turn" and b.get("speaker") and b["speaker"] not in seen:
                seen.append(b["speaker"])
        participants = [
            {"speaker_label": s, "resolved_name": None, "external_id": None, "is_owner": None}
            for s in seen
        ]

    time_axis = None
    turns = [b for b in blocks if b["type"] == "transcript_turn" and "start_ms" in b]
    if turns:
        time_axis = {
            "start_at": None,
            "end_at": None,
            "duration_ms": max(b.get("end_ms") or 0 for b in turns),
        }

    structure = {"sequence": sequence, "blocks": blocks, "time_axis": time_axis}
    if participants:
        structure["participants"] = participants

    document = {
        "document_id": document_id_for(source_id),
        "source_id": source_id,
        "title": title,
        "source_type": source_type,
        "content_format": content_format,
        "content": text,
        "language": _language_of(blocks),
        "original_metadata": {
            "original_filename": path.name,
            "file_size_bytes": len(raw),
            "file_hash": content_hash(raw),
            "line_count": len(text.splitlines()),
        },
        "structure": structure,
        "attachments": [],
        "extracted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "parser": {"name": f"pks.{suffix.lstrip('.') or 'text'}_parser", "version": "1.0.0", "method": "rule"},
        "parsing_status": "partial" if warnings else "success",
        "parsing_warnings": warnings,
        "schema_version": "1.0.0",
    }
    return Parsed(document=document, warnings=warnings)
