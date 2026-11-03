"""Model-driven knowledge extraction.

The rule-based approach (split on length, guess a type from keywords) was
tried first and abandoned: it mislabelled ~70% of content types and
promoted diaries, poems and device notes into "knowledge". What replaced
it is a narrow contract:

* the model may only quote or paraphrase what is in the chunk;
* every item must cite at least one ``block_id`` that exists;
* empty output is a valid answer — no quota to fill.

Everything else is checked afterwards by ``validate.py``, not trusted.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ids import new_knowledge_id, text_hash

DEFAULT_BASE = os.environ.get("PKS_API_BASE", "https://api.openai.com/v1")
DEFAULT_MODEL = os.environ.get("PKS_MODEL", "gpt-4o-mini")

SYSTEM = """You extract atomic, reusable knowledge units from documents.

Hard rules:
1. Use only what the text says. Do not add facts, do not smooth prose.
2. Keep conditions, limits and counterexamples with the claim.
3. Never emit headings, fragments, prompts or transitions as items.
4. Never turn a diary entry, poem or feeling into a theory.
5. Never restate a question as a fact.
6. Never present a summary as a quotation.
7. Do not split one idea into several items to raise the count.
8. Every item cites at least one [block_id] that appears in the input.
9. A diary entry, log line or passing mood is not knowledge. Skip it unless
   the text states a reusable conclusion the author clearly stands behind.
10. One incident is not a rule. Do not generalise from a single episode.

Reply with JSON only."""

PROMPT = """Document: {title}
Source kind: {kind}

Text, one block per line, each prefixed with its id:
{blocks}

Extract items. Field rules:
- content_type: {types}
- domain: choose the field the *subject matter* belongs to, not the activity
  of reading or listening. Course content about accounting is D03 even though
  it arrived as a lecture. Options:
  {domains}
- attribution: user (the author speaking) | other_speaker (lecturer, peer) |
  external_author (cited book or article) | ai_inference (your own synthesis) | unknown
- epistemic_status: verified_fact only for externally checkable facts stated
  in the text; otherwise opinion, unverified_claim, question, plan or decision.
- confidence: how sure you are that this extraction is faithful, 0..1.
- context_note: which premises you preserved.

Return {{"items": [{{"title": "...", "content": "...", "block_ids": ["blk_0001"],
"domain": "...", "content_type": "...", "attribution": "...",
"attribution_name": null, "epistemic_status": "...", "confidence": 0.0,
"tags": ["..."], "context_note": "...", "rationale": "..."}}]}}

Return an empty list if nothing qualifies."""


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE
    api_key: str = os.environ.get("PKS_API_KEY", "")
    model: str = DEFAULT_MODEL
    timeout: int = 120
    retries: int = 3

    def ready(self) -> bool:
        return bool(self.api_key)


def call_llm(config: LLMConfig, system: str, user: str, max_tokens: int = 3000) -> dict:
    """POST to an OpenAI-compatible chat completion endpoint."""
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload).encode("utf-8")
    url = config.base_url.rstrip("/") + "/chat/completions"
    last: Exception | None = None

    for attempt in range(config.retries):
        try:
            request = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config.api_key}",
                },
            )
            with urllib.request.urlopen(request, timeout=config.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return json.loads(data["choices"][0]["message"]["content"])
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after {config.retries} attempts: {last}")


def chunk(document: dict, budget: int = 5000) -> list[list[dict]]:
    """Group blocks into chunks under a character budget.

    Splitting happens on block boundaries only, so no block is ever cut in
    half and every citation remains resolvable.
    """
    blocks = [
        b for b in document["structure"]["blocks"]
        if b.get("text") and b["type"] not in ("separator", "heading")
    ]
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for block in blocks:
        text = block["text"]
        if size + len(text) > budget and current:
            chunks.append(current)
            current, size = [], 0
        current.append(block)
        size += len(text)
    if current:
        chunks.append(current)
    return chunks


def render(blocks: list[dict]) -> str:
    lines = []
    for block in blocks:
        text = re.sub(r"\s+", " ", block.get("text") or "").strip()
        lines.append(f"[{block['block_id']}] {text}")
    return "\n".join(lines)


def _enums(schema_path: Path) -> tuple[list[str], str]:
    """Read the controlled vocabularies out of the schema itself.

    Domain codes carry human labels in ``x-labels``; passing the labels to
    the model matters, because a bare list like ``D01..D11`` gives it no
    way to choose and it will default to the first entry.
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    types = schema["properties"]["content_type"]["enum"]
    domain_def = schema["$defs"]["domain_code"]
    labels = domain_def.get("x-labels") or {}
    domains = "; ".join(
        f"{code}={labels[code]}" if code in labels else code
        for code in domain_def["enum"]
    )
    return types, domains


def extract(
    document: dict,
    config: LLMConfig,
    schema_path: Path,
    material_type: str = "unknown",
) -> tuple[list[dict], list[str]]:
    """Return ``(items, errors)`` for one document."""
    types, domains = _enums(schema_path)
    block_map = {b["block_id"]: b for b in document["structure"]["blocks"]}
    items: list[dict] = []
    errors: list[str] = []

    for index, blocks in enumerate(chunk(document)):
        prompt = PROMPT.format(
            title=document["title"],
            kind=material_type,
            blocks=render(blocks),
            types=", ".join(types),
            domains=", ".join(domains),
        )
        try:
            payload = call_llm(config, SYSTEM, prompt)
        except RuntimeError as exc:
            errors.append(f"chunk {index}: {exc}")
            continue

        for raw in payload.get("items") or []:
            item = normalise(document, raw, block_map, types, domains, material_type)
            if item is None:
                continue
            items.append(item)

    return items, errors


def normalise(
    document: dict,
    raw: dict,
    block_map: dict[str, dict],
    types: list[str],
    domains: list[str],
    material_type: str,
) -> dict | None:
    """Coerce one model item into the schema, or drop it.

    Dropping is deliberate: an item with no resolvable citation cannot be
    verified later, so it does not belong in the store.
    """
    ids = [b for b in (raw.get("block_ids") or []) if b in block_map]
    content = (raw.get("content") or "").strip()
    title = (raw.get("title") or "").strip()
    if not ids or len(content) < 10 or not title:
        return None

    domain_codes = [d.split("=")[0].strip() for d in domains.split(";")]
    domain = raw.get("domain") if raw.get("domain") in domain_codes else domain_codes[0]
    ctype = raw.get("content_type") if raw.get("content_type") in types else "opinion"
    attribution = raw.get("attribution")
    if attribution not in ("user", "other_speaker", "external_author", "ai_inference", "unknown"):
        attribution = "unknown"
    epistemic = raw.get("epistemic_status")
    if epistemic not in ("verified_fact", "unverified_claim", "opinion", "hypothesis",
                         "question", "decision", "plan", "prediction", "unknown"):
        epistemic = "unknown"
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    # Epistemic guard. Models over-use verified_fact, so implausible labels
    # are downgraded at write time instead of failing validation later.
    guard_note = ""
    if epistemic == "verified_fact":
        if attribution == "user":
            epistemic = "opinion"
            guard_note = "downgraded verified_fact -> opinion (self-authored)"
        elif ctype not in ("fact", "concept"):
            epistemic = "unverified_claim"
            guard_note = f"downgraded verified_fact -> unverified_claim (type={ctype})"

    refs = []
    for block_id in ids[:3]:
        block = block_map[block_id]
        quote = (block.get("text") or "")[:2000]
        locator = {"kind": "block_index", "block_id": block_id}
        if block["type"] == "transcript_turn" and block.get("start_ms") is not None:
            locator = {"kind": "timestamp", "timestamp_ms": block["start_ms"]}
        refs.append({
            "source_id": document["source_id"],
            "document_id": document["document_id"],
            "locator": locator,
            "quote": quote,
            "quote_hash": text_hash(quote),
            "note": f"block {block_id}",
        })

    knowledge_id = new_knowledge_id()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "knowledge_id": knowledge_id,
        "title": title[:200],
        "content": content[:20000],
        "content_form": "paraphrase",
        "domain": [domain],
        "content_type": ctype,
        "tags": [str(t)[:40] for t in (raw.get("tags") or [])][:12],
        "source_refs": refs,
        "traceability": "sourced",
        "attribution": [{
            "kind": attribution,
            "name": (raw.get("attribution_name") or None),
            "source_ref_index": 0,
            "note": (raw.get("context_note") or None),
        }],
        "epistemic_status": epistemic,
        "confidence": round(confidence, 2),
        "related_knowledge_ids": [],
        "related_projects": [],
        "language": document.get("language"),
        "created_at": now,
        "extracted_at": now,
        "updated_at": None,
        "review_status": "needs_review",
        "review": {
            "reviewer": None,
            "reviewed_at": None,
            "decision": None,
            "required_reasons": (["model_uncertain"] if confidence < 0.6
                                 else ["epistemic_downgraded"] if guard_note
                                 else ["manual_spot_check"]),
            "note": (raw.get("rationale") or "")[:200],
        },
        "dedupe_fingerprint": text_hash(f"{content}|{domain}|{ctype}"),
        "processing_history": [{
            "step": "extract",
            "actor_type": "model",
            "actor_name": DEFAULT_MODEL,
            "actor_version": None,
            "at": now,
            "input_ref": document["document_id"],
            "output_ref": knowledge_id,
            "result": "success",
            "confidence": round(confidence, 2),
            "notes": f"material={material_type}; blocks={','.join(ids)}"
                     + (f"; {guard_note}" if guard_note else ""),
        }],
        "schema_version": "1.0.0",
    }
