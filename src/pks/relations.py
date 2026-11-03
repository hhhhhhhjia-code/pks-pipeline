"""Relatedness without merging.

Two items that look alike are not the same item. A person can hold two
incompatible views six months apart, and that pair is *evidence*, not
noise. So this module only ever produces labelled, one-directional
candidate edges. Nothing is deleted, nothing is rewritten.

Recall comes from cheap blocking (same domain, shared tags, same source
document). Precision comes from a classifier — rule-based by default,
model-backed when an API key is present. An empty edge list is a normal
outcome.

Edge types
----------
``exact_duplicate``    byte-identical restatement
``semantic_duplicate`` same claim, different words
``complement``         each adds something the other lacks
``conflict``           mutually exclusive claims
``hierarchy``          A is a special case of B
``method_application`` A is a method, B applies it
``question_answer``    A asks what B answers
``evolution``          same question, different answers over time

Only ``hierarchy``, ``method_application``, ``question_answer`` and
``evolution`` are directional. The rest are symmetric and get mirrored in
the lookup index, not duplicated in storage.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

SYMMETRIC = {"exact_duplicate", "semantic_duplicate", "complement", "conflict", "same_topic"}
DIRECTED = {"hierarchy", "method_application", "question_answer", "evolution"}

SYSTEM = """You compare two knowledge items and label their relationship.
Answer only with JSON. Never merge, rewrite or rank them."""

PROMPT = """A: {title_a}
{content_a}

B: {title_b}
{content_b}

Pick one label:
exact_duplicate, semantic_duplicate, complement, conflict, hierarchy,
method_application, question_answer, evolution, same_topic, unrelated

Rules: similar wording alone is `unrelated`. Different answers to the same
question at different times are `evolution`, not `conflict`.

Return {{"relation": "...", "confidence": 0.0, "reason": "..."}}"""


@dataclass
class Candidate:
    a: str
    b: str
    shared_tags: int
    same_document: bool
    score: float = 0.0


def _tags(item: dict) -> set[str]:
    return {t.lower() for t in (item.get("tags") or []) if t}


def block_candidates(items: list[dict], max_pairs: int = 400) -> list[Candidate]:
    """Cheap recall: only compare items likely to be related.

    Domain equality is required — an economics item and a language item
    rarely share a claim, and comparing everything is O(n^2) for nothing.
    """
    by_domain: dict[str, list[dict]] = {}
    for item in items:
        by_domain.setdefault(item["domain"][0], []).append(item)

    found: list[Candidate] = []
    for group in by_domain.values():
        if len(group) < 2:
            continue
        for a, b in combinations(group, 2):
            tags_a, tags_b = _tags(a), _tags(b)
            shared = len(tags_a & tags_b)
            same_doc = a["source_refs"][0]["document_id"] == b["source_refs"][0]["document_id"]
            if shared >= 2 or (shared >= 1 and same_doc):
                score = shared + (1.0 if same_doc else 0.0)
                found.append(Candidate(a["knowledge_id"], b["knowledge_id"], shared, same_doc, score))

    found.sort(key=lambda c: -c.score)
    return found[:max_pairs]


def classify_rule(a: dict, b: dict) -> tuple[str, float, str]:
    """Offline fallback.

    Deliberately conservative: it only claims a strong relation when the
    evidence is textual (identical content, identical title, question next
    to answer). Everything else is capped low so it can be filtered out.
    """
    fingerprint_a = a.get("dedupe_fingerprint")
    if fingerprint_a and fingerprint_a == b.get("dedupe_fingerprint"):
        return "exact_duplicate", 0.95, "identical fingerprint"

    content_a, content_b = a["content"].strip(), b["content"].strip()
    if content_a == content_b:
        return "exact_duplicate", 0.95, "identical content"

    title_a, title_b = a["title"].strip(), b["title"].strip()
    if title_a == title_b:
        return "semantic_duplicate", 0.8, "identical title, different body"

    types = {a["content_type"], b["content_type"]}
    if types in ({"question", "fact"}, {"question", "theory"}, {"question", "concept"}):
        return "question_answer", 0.55, "question paired with an answer-shaped item"

    same_document = a["source_refs"][0]["document_id"] == b["source_refs"][0]["document_id"]
    shared = len(_tags(a) & _tags(b))

    if same_document and shared >= 2:
        return "complement", 0.55, "two facets of the same source, shared tags"
    if not same_document and shared >= 3:
        return "same_topic", 0.5, "same tags across sources"
    if same_document:
        return "same_topic", 0.4, "same source document"
    return "unrelated", 0.2, "no textual evidence of a relation"


def classify_llm(a: dict, b: dict, config) -> tuple[str, float, str]:
    from .extract import call_llm

    prompt = PROMPT.format(
        title_a=a["title"], content_a=a["content"][:600],
        title_b=b["title"], content_b=b["content"][:600],
    )
    try:
        payload = call_llm(config, SYSTEM, prompt, max_tokens=300)
    except RuntimeError:
        return classify_rule(a, b)
    relation = payload.get("relation", "unrelated")
    if relation not in SYMMETRIC | DIRECTED | {"unrelated"}:
        relation = "unrelated"
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return relation, confidence, (payload.get("reason") or "")[:120]


def build_edges(
    items: list[dict],
    config=None,
    min_confidence: float = 0.5,
    max_pairs: int = 400,
) -> tuple[list[dict], dict]:
    """Return ``(edges, stats)``. Edges are deduplicated and orientation-normalised."""
    by_id = {item["knowledge_id"]: item for item in items}
    candidates = block_candidates(items, max_pairs)

    edges: dict[tuple[str, str], dict] = {}
    dropped = Counter()

    for cand in candidates:
        a, b = by_id[cand.a], by_id[cand.b]
        if config is not None and getattr(config, "ready", lambda: False)():
            relation, confidence, reason = classify_llm(a, b, config)
        else:
            relation, confidence, reason = classify_rule(a, b)

        if relation == "unrelated" or confidence < min_confidence:
            dropped[relation] += 1
            continue
        if cand.a == cand.b:
            dropped["self_loop"] += 1
            continue

        key = (cand.a, cand.b) if cand.a < cand.b else (cand.b, cand.a)
        if key in edges:
            if confidence > edges[key]["confidence"]:
                edges[key] = {
                    "a": key[0], "b": key[1], "relation": relation,
                    "confidence": round(confidence, 2), "reason": reason,
                }
            continue
        edges[key] = {
            "a": key[0], "b": key[1], "relation": relation,
            "confidence": round(confidence, 2), "reason": reason,
        }

    result = sorted(edges.values(), key=lambda e: -e["confidence"])
    stats = {
        "candidates": len(candidates),
        "edges": len(result),
        "dropped": dict(dropped),
        "by_relation": dict(Counter(e["relation"] for e in result)),
    }
    return result, stats


def index(edges: list[dict], item_ids: list[str]) -> dict[str, dict[str, list[dict]]]:
    """Build an in/out adjacency index without storing reverse edges twice."""
    graph = {item_id: {"in": [], "out": []} for item_id in item_ids}
    for edge in edges:
        a, b, relation, confidence = edge["a"], edge["b"], edge["relation"], edge["confidence"]
        directed = relation in DIRECTED
        if a in graph and b in graph:
            graph[a]["out"].append({"id": b, "relation": relation,
                                    "confidence": confidence, "directed": directed})
            graph[b]["in"].append({"id": a, "relation": relation,
                                   "confidence": confidence, "directed": directed})
            if not directed:
                graph[b]["out"].append({"id": a, "relation": relation,
                                        "confidence": confidence, "directed": False})
                graph[a]["in"].append({"id": b, "relation": relation,
                                       "confidence": confidence, "directed": False})
    return graph


def check_symmetry(edges: list[dict], graph: dict) -> dict:
    """Verify the index is internally consistent. Should always be zero."""
    missing = 0
    mirrored = 0
    for edge in edges:
        a, b, relation = edge["a"], edge["b"], edge["relation"]
        if relation in SYMMETRIC:
            if not any(x["id"] == a for x in graph.get(b, {}).get("out", [])):
                missing += 1
        elif relation in DIRECTED:
            if any(x["id"] == a and x["relation"] == relation
                   for x in graph.get(b, {}).get("out", [])):
                mirrored += 1
    return {"symmetric_missing": missing, "directed_mirrored": mirrored}


def dump(edges: list[dict], graph: dict, directory) -> None:
    from pathlib import Path

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "edges.json").write_text(
        json.dumps(edges, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
