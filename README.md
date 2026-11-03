# pks — personal knowledge pipeline

A small, inspectable pipeline that turns raw notes, transcripts and subtitles
into **reviewable knowledge items, each traceable to a specific line or
timestamp in the original file**.

It is not a note-taking app and not an AI summariser. It is the layer that sits
between a pile of exports and a place you trust enough to search.

```
raw file  ──▶  parsed document  ──▶  knowledge item  ──▶  relations
(immutable)    (structure only)      (claims + sources)    (candidate edges)
```

[中文说明](README.zh-CN.md)

---

## Why

Every "second brain" project dies the same death: the extracted notes stop
being trustworthy, so you stop reading them. Two specific failure modes cause
it.

**Claims lose their address.** A summary says "double-entry bookkeeping survived
because it localises error detection". Six months later you cannot find whether
that was the lecturer, a textbook, or the model's own synthesis. Once that
happens, nothing in the store can be checked, so nothing is.

**Extraction inflates.** Feed a diary entry to a model with an instruction to
"extract knowledge" and you get three confident bullet points about the
author's mood, labelled `theory`. Do that ten thousand times and the corpus is
mostly noise.

This pipeline is built around refusing both. Structure is separated from
meaning. Every claim carries a resolvable pointer. Uncertainty is a first-class
field, not a footnote.

---

## The three layers

| Layer | Holds | Mutable? | Rebuildable from |
|---|---|---|---|
| **source** | the file exactly as exported, plus a manifest row | never | — |
| **parsed** | `content` (verbatim text) + `structure.blocks` (typed, located segments) | yes | source |
| **knowledge** | atomic claims with provenance, attribution, epistemic status | appended | parsed |

Each layer has a JSON Schema with conditional constraints, not just field
types. A few examples of what the schema itself refuses:

- `content_form: verbatim` without `verbatim_quotes` → rejected
- `traceability: sourced` with an empty `source_refs` → rejected
- `traceability: untraceable` **and** `review_status: approved` → rejected
- `review_status: approved` without a `review` record → rejected
- a `transcript_turn` block missing `speaker` or `start_ms` → rejected

The point is that the invariants survive contact with code you have not written
yet.

---

## Design decisions worth arguing about

| Decision | Alternative rejected | Why |
|---|---|---|
| `source_id` assigned once, never recomputed | hash of content, or the file path | Editing one typo would break every reference. Moving the folder would break all of them at once. |
| `document_id` derived from `source_id` | random UUID | Re-parsing must overwrite the same document. Deterministic IDs make that free. |
| Crockford base32 for ID suffixes | hex, base64 | No `I`/`L`/`O`/`U`. Survives being read aloud, copied by hand, or OCR'd. |
| `content` kept verbatim next to `blocks` | store blocks only | Blocks are a view. The view will have bugs; the original must not. |
| `source_type` is a slug, not an enum | closed enum | Adding a new source must never require a schema migration. Learned the hard way — see [lessons](docs/lessons.md). |
| Epistemic status as a separate axis from content type | one "type" field | "Fact" is a claim about the world; "opinion" is a claim about the author. Collapsing them is how models launder speculation into truth. |
| Relations are labelled candidates | auto-merge near-duplicates | Two contradictory beliefs held six months apart are *evidence*, not noise. Merging destroys the timeline. |
| Empty extraction is a valid answer | target N items per document | A quota guarantees padding. |

---

## Quickstart

```bash
pip install -e .          # or: pip install -r requirements.txt

# 1. parse: offline, no model needed
pks parse examples/corpus -o out/parsed

# 2. extract: needs an OpenAI-compatible endpoint
export PKS_API_KEY=sk-...
export PKS_API_BASE=https://api.openai.com/v1   # optional
export PKS_MODEL=gpt-4o-mini                     # optional
pks extract out/parsed -o out/items

# 3. relate: offline by default, --llm for model labels
pks relate out/items -o out/relations

# 4. verify: schema, provenance, epistemic sanity
pks validate out/items --documents out/parsed --relations out/relations

# 5. look at what you got
pks inspect out/items
```

`examples/corpus/` ships four synthetic files: a technical note, a lecture
subtitle track with two speakers, a meeting retro in WebVTT, and a diary page
that should mostly **not** become knowledge.

---

## What an item actually looks like

A real (truncated) output from the sample corpus:

```jsonc
{
  "knowledge_id": "kn_01M6GGF48JFR80MY2R4GWK0Q89",
  "title": "Double-entry bookkeeping survived because it makes error detection local",
  "content": "Double-entry bookkeeping survived for six centuries while other accounting systems did not because it makes error detection local: every transaction touches two accounts, so a mistake shows up as an imbalance rather than as a wrong total nobody notices.",
  "content_form": "paraphrase",
  "domain": ["D03"],
  "content_type": "theory",
  "source_refs": [
    {
      "document_id": "doc_local_20261103_MWJEQF_01",
      "locator": { "kind": "timestamp", "timestamp_ms": 12480 },
      "quote": "The short answer is that it makes error detection local. Every transaction ...",
      "quote_hash": "sha256:ea6b29b8..."
    }
  ],
  "attribution": [{ "kind": "other_speaker", "name": null }],
  "epistemic_status": "unverified_claim",
  "confidence": 0.9,
  "review_status": "needs_review"
}
```

Read that as: *the lecturer said this at 00:00:12.480, we are not treating it as
established fact, and nobody has confirmed it yet.*

`quote_hash` is the guard against silent drift. If the transcript is re-exported
and the wording shifts, the stored hash stops matching and `pks validate`
reports `quote_mismatch` instead of quietly serving the old claim.

---

## Commands

| Command | Network | What it does |
|---|---|---|
| `pks parse <dir>` | no | Markdown / SRT / VTT / TXT → parsed documents |
| `pks extract <parsed>` | yes | Blocks → knowledge items, one model call per chunk |
| `pks relate <items>` | optional | Blocking + labelling → candidate edges, no merging |
| `pks validate <items>` | no | Schema, provenance, epistemic guard, edge sanity |
| `pks inspect <items>` | no | Distribution by domain / type / attribution |

`extract` never sends more than one chunk at a time, and chunk boundaries always
fall on block boundaries — so no citation can point into a fragment that was
never sent.

---

## Layout

```
schemas/     three JSON Schemas; the contract everything else obeys
rules/       domain taxonomy, content types, governance rules (prose)
src/pks/
  ids.py       stable identifiers and hashed quotes
  parse.py     md / srt / vtt / txt → typed, located blocks
  extract.py   chunking, prompting, normalisation, epistemic guard
  relations.py blocking, labelling, in/out index
  validate.py  the checks that would otherwise be skipped
  cli.py       entry point
examples/    synthetic corpus + sample output
docs/        architecture notes and a post-mortem of what went wrong first
```

---

## What this deliberately does not do

- **No storage engine.** JSON files on disk. Add a database when you have a
  reason, not before.
- **No embeddings.** Relations are found by blocking on domain and tags, then
  labelled by a model. Vector recall is a plug-in point, not a dependency —
  and the honest reason is in the lessons doc.
- **No auto-approval.** The pipeline can mark items `needs_review`; only a
  human can mark them `approved`. Schema-enforced.
- **No merge.** Duplicates become edges. Deletion is not a feature.
- **No Notion, no sync, no UI.** Those are downstream problems.

---

## Known limits

1. Domain assignment is the weakest part. A model given eleven codes and short
   labels will still drift; expect to review domain labels before you trust
   them. Passing the *names* rather than bare codes measurably helps (see
   lessons) but does not fix it.
2. `relations` recall is limited by blocking. Cross-domain connections are
   invisible to it by construction. That is a deliberate trade, not a bug.
3. Chunking is by character budget. Very long single blocks (a 3000-word
   unbroken paragraph) degrade quality.
4. Nothing here evaluates extraction quality. Measuring that needs a labelled
   sample, which needs a human.

## License

MIT
