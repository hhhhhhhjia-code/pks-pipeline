"""Checks that are cheaper to automate than to trust.

Three families:

* **Schema** — structural conformance.
* **Provenance** — the cited block exists and the stored quote still
  matches it byte for byte. A mismatch means the source moved under us.
* **Epistemic guard** — ``verified_fact`` is a strong claim. It is refused
  for self-authored statements and for content types that cannot carry a
  verifiable fact, because models over-assign it.

Every check returns issues instead of raising, so one bad record does not
hide the rest.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - guidance for a missing dep
    Draft202012Validator = None  # type: ignore

from .ids import text_hash


@dataclass
class Issue:
    kind: str
    target: str
    detail: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target, "detail": self.detail}


def load_schema(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_schema(records: list[dict], schema: dict) -> list[Issue]:
    if Draft202012Validator is None:
        return [Issue("dependency_missing", "jsonschema", "pip install jsonschema")]
    validator = Draft202012Validator(schema)
    issues = []
    for record in records:
        for error in validator.iter_errors(record):
            path = "/".join(str(p) for p in error.path) or "<root>"
            issues.append(Issue("schema_invalid",
                                record.get("knowledge_id") or record.get("document_id", "?"),
                                f"{path}: {error.message}"))
    return issues


def check_unique_ids(records: list[dict], key: str) -> list[Issue]:
    seen: dict[str, int] = {}
    for record in records:
        value = record.get(key)
        if value:
            seen[value] = seen.get(value, 0) + 1
    return [Issue("duplicate_id", key, f"{value} appears {count} times")
            for value, count in seen.items() if count > 1]


def check_provenance(items: list[dict], documents: dict[str, dict]) -> list[Issue]:
    """Every citation must resolve to a block, and the quote must still match."""
    issues = []
    for item in items:
        item_id = item["knowledge_id"]
        if not item.get("source_refs"):
            issues.append(Issue("no_source", item_id, "item has no source_refs"))
            continue
        for ref in item["source_refs"]:
            document = documents.get(ref.get("document_id"))
            if document is None:
                issues.append(Issue("dangling_document", item_id, str(ref.get("document_id"))))
                continue
            blocks = {b["block_id"]: b for b in document["structure"]["blocks"]}
            block_id = ref.get("locator", {}).get("block_id")
            if block_id and block_id not in blocks:
                issues.append(Issue("dangling_block", item_id, str(block_id)))
                continue
            if block_id:
                expected = (blocks[block_id].get("text") or "")[:2000]
                if ref.get("quote") != expected:
                    issues.append(Issue("quote_mismatch", item_id, f"block {block_id}"))
                elif ref.get("quote_hash") and ref["quote_hash"] != text_hash(expected):
                    issues.append(Issue("quote_hash_mismatch", item_id, f"block {block_id}"))
    return issues


FACT_CAPABLE = {"fact", "concept"}


def check_epistemic(items: list[dict]) -> list[Issue]:
    """Flag implausible ``verified_fact`` labels."""
    issues = []
    for item in items:
        if item.get("epistemic_status") != "verified_fact":
            continue
        item_id = item["knowledge_id"]
        if item.get("content_type") not in FACT_CAPABLE:
            issues.append(Issue(
                "epistemic_overreach", item_id,
                f"verified_fact on content_type={item.get('content_type')}"))
        kind = (item.get("attribution") or [{}])[0].get("kind")
        if kind == "user":
            issues.append(Issue(
                "epistemic_overreach", item_id,
                "verified_fact on a self-authored statement"))
    return issues


def check_shape(items: list[dict], min_chars: int = 10, max_title: int = 200) -> list[Issue]:
    issues = []
    for item in items:
        item_id = item["knowledge_id"]
        if len((item.get("content") or "").strip()) < min_chars:
            issues.append(Issue("empty_content", item_id, "content too short"))
        if len(item.get("title") or "") > max_title:
            issues.append(Issue("title_too_long", item_id, f"{len(item['title'])} chars"))
        if item.get("review_status") == "approved":
            issues.append(Issue("premature_approval", item_id,
                                "pipeline must not self-approve"))
    return issues


def check_edges(edges: list[dict], item_ids: set[str]) -> list[Issue]:
    issues = []
    for edge in edges:
        a, b = edge.get("a"), edge.get("b")
        if a == b:
            issues.append(Issue("self_edge", str(a), "edge points at itself"))
        if a not in item_ids or b not in item_ids:
            issues.append(Issue("dangling_edge", f"{a}->{b}", "endpoint missing"))
    return issues


def run(
    items: list[dict],
    documents: dict[str, dict],
    schema_path: Path,
    edges: list[dict] | None = None,
) -> dict:
    """Run every check and return a summary dict."""
    schema = load_schema(schema_path)
    issues: list[Issue] = []
    issues += check_schema(items, schema)
    issues += check_unique_ids(items, "knowledge_id")
    issues += check_provenance(items, documents)
    issues += check_epistemic(items)
    issues += check_shape(items)

    if edges:
        issues += check_edges(edges, {i["knowledge_id"] for i in items})

    return {
        "items": len(items),
        "documents": len(documents),
        "edges": len(edges or []),
        "issues": len(issues),
        "by_kind": dict(Counter(i.kind for i in issues)),
        "detail": [i.as_dict() for i in issues],
    }
