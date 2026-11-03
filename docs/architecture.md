# Architecture

## Invariants

The pipeline is small, but four properties are non-negotiable. Everything else
is implementation detail.

1. **A source file is never modified.** No cleaning, no re-encoding, no
   format conversion in place. Parsing reads bytes and writes new files.
2. **Every stored claim resolves to a location in a source.** Not "the meeting",
   not "the note" — a block id, and behind it a line range or a millisecond.
3. **Rebuilding is safe.** `parsed` and `knowledge` can be regenerated from
   `source`. If a rebuild is not safe, the derivation is wrong.
4. **Nothing is approved by the pipeline.** `review_status: approved` requires a
   named reviewer and a timestamp. The schema enforces this with a conditional.

## Data flow

```
                 ┌──────────────────────────────┐
                 │ source                       │
                 │ file bytes + manifest row     │  never mutated
                 └──────────────┬───────────────┘
                                │ parse (deterministic, offline)
                 ┌──────────────▼───────────────┐
                 │ parsed_document              │
                 │ content: verbatim text        │  rebuildable
                 │ structure.blocks: typed,      │
                 │   each with a locator         │
                 └──────────────┬───────────────┘
                                │ extract (model, chunked)
                 ┌──────────────▼───────────────┐
                 │ knowledge_item               │
                 │ claim + source_refs +         │  append-only
                 │ attribution + epistemic       │
                 └──────────────┬───────────────┘
                                │ relate (blocking + labelling)
                 ┌──────────────▼───────────────┐
                 │ edges                        │
                 │ labelled candidates, not      │  additive
                 │ merges                        │
                 └──────────────────────────────┘
```

Arrows only point down. A knowledge item never writes to a parsed document, and
a parsed document never writes to a source. This is what makes partial rebuilds
possible: change the extraction prompt, rerun `extract`, and the parsed layer is
untouched.

## Identifiers

| ID | Shape | Assignment | Stability |
|---|---|---|---|
| `source_id` | `src_<type>_<YYYYMMDD>_<6>` | first import | permanent |
| `document_id` | `doc_<source minus src_>_<nn>` | derived | deterministic |
| `attachment_id` | `att_<nnn>` | within document | rebuilt with document |
| `knowledge_id` | `kn_<26-char ULID>` | creation | permanent |

Why not use a content hash as the primary key: fixing one typo changes the key,
and every reference to that item — relations, review notes, external bookmarks —
breaks at once. Why not the file path: moving a folder does the same thing.

The date segment in `source_id` is the import date, not the content date. Content
dates are unreliable at import time and belong in `created_at` with an explicit
`created_at_source`.

## Blocks and locators

A block is the smallest addressable unit. It carries a type, its text, and a
`locator`.

| Source shape | Locator kind | Example |
|---|---|---|
| Markdown, txt | `line_range` | lines 12–14 |
| SRT, VTT, transcripts | `timestamp` | `timestamp_ms: 12480` |
| PDF (future) | `page` | page 7 |
| Tables (future) | `table_cell` | row 3, col "total" |

Blocks are generated with a stable positional id (`blk_0004`) and are never
reordered within a document version. Re-parsing produces a new document at the
same `document_id`, so block ids may shift between parser versions — which is
exactly why every stored quote also carries a `quote_hash`. If the block moved
and the text changed, the hash catches it.

## Extraction

Chunking is by character budget, cut on block boundaries only. The model sees
one chunk at a time, with each line prefixed by its block id.

The output contract is deliberately narrow:

- an item must cite at least one block id present in the chunk;
- content shorter than 10 characters is dropped;
- empty output is accepted.

Normalisation is a whitelist: unknown domain codes, content types, attribution
kinds and epistemic values are replaced with safe defaults rather than trusted.
Then the epistemic guard runs before writing.

The guard exists because prompt instructions do not survive generation. Stating
"do not use verified_fact for opinions" reduces the rate; enforcing it in code
afterwards removes the class.

## Relations

Two stages, deliberately separated:

**Blocking** — cheap recall. Compare only items in the same domain that share at
least two tags, or one tag and a source document. This keeps the pair count
linear-ish instead of quadratic, at the cost of never finding a connection that
crosses domains. That cost is accepted; see `docs/lessons.md`.

**Labelling** — precision. Rules first (identical fingerprint, identical
content, identical title, question-answer adjacency), model second when a key is
present. The rule path is conservative on purpose: it returns `unrelated` unless
there is textual evidence, because a wrong `conflict` edge is worse than a
missing one.

Storage splits direction from relation type. Symmetric relations
(`complement`, `conflict`, `semantic_duplicate`, `same_topic`) are written to
both endpoints in the in/out index but stored once. Directed relations
(`hierarchy`, `method_application`, `question_answer`, `evolution`) keep their
orientation. `check_symmetry` verifies both properties and should always
report zero.

## Validation

Checks are grouped so a failure tells you which layer to fix:

| Group | Catches | Fix belongs in |
|---|---|---|
| schema | malformed records | extraction / normalisation |
| provenance | dangling block, quote drift | parsing or source drift |
| epistemic | implausible `verified_fact` | the guard |
| shape | empty content, oversized title, self-approval | extraction |
| edges | self loops, dangling endpoints | relation pass |

`validate` returns issues rather than raising, so one bad record does not mask
the other 2,531.
