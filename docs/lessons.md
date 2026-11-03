# What went wrong first

This pipeline is the second attempt. The first one was a rule-based extractor,
and it was wrong in ways that were only visible after measuring. These notes
exist so the next person does not rebuild it.

Numbers below come from a real corpus of 181 documents (156 personal notes, 25
meeting transcripts), sampled and hand-checked.

---

## 1. Rules cannot tell you what a document *is*

The first version split documents into paragraphs, kept the ones between 40 and
320 characters, and guessed a content type from keywords. Sampling 40 items
against their originals:

| Verdict | Count |
|---|---|
| usable as-is | 2 |
| needs a small fix | 15 |
| needs re-extraction | 7 |
| should not exist at all | 16 |

**40% of "knowledge" was not knowledge.** Poems, school essays, phone setup
notes and a to-do list had all been promoted into the store because they
happened to fall in the length window.

The keyword approach also produced systematic mislabelling — 29 of 40 items had
the wrong content type. The reason is structural: the last rule in the chain was
`theory` as a fallback, so anything that missed every keyword became a theory.
Class attendance lists became theory. A British school-system explainer became
theory.

**Fix:** classify the *material* before extracting from it. Six kinds —
knowledge note, reading excerpt, thinking journal, creative work,
administrative note, class session — and only some of them feed extraction.
On the same corpus this moved 43 of 181 documents out of the extraction path
entirely.

---

## 2. The extractor was reading the wrong artefact

Meeting transcripts arrived with two things in them: an AI-generated chapter
outline, and the verbatim speaker turns. The first extractor used the outline,
because it was already in neat paragraphs.

Every resulting item began "This section explains...". That is a summary of a
discussion, not a claim from it. The item was two removes from anything anyone
said, and the person who said it had been erased.

The verbatim turns — 2,624 of them — were sitting in the same file, unused.

**Fix:** never extract from generated summaries. Treat them as navigation. Pull
claims from speaker turns, and keep the timestamp.

---

## 3. A model asked to find duplicates will invent them

On a 2,532-item run, the relation pass returned 206 `exact_duplicate` edges.
All 206 were an item paired with itself. The prompt said "compare A and B"; the
model, given pairs of similar text, sometimes answered about one.

Nothing downstream noticed, because "duplicate" is an expected label and the
count looked plausible. It surfaced only when an adjacency index was built and
206 edges pointed at their own origin.

**Fix:** validate graph output against the input. An edge whose endpoints are
equal is not a duplicate, it is a bug. Reject before deduplicating, and treat
suspiciously round distributions as a smell.

---

## 4. Closed enums are a migration debt

`source_type` was an enum of six platforms. The seventh platform to arrive could
not be stored without editing the schema, which meant bumping the version, which
meant every historical record needed revalidation against a schema whose
`schema_version` was now a constant.

The workaround was to store new sources as `"other"` with the real platform name
buried in `original_metadata`. Six months later, nobody queries
`original_metadata`.

**Fix in this repo:** `source_type` is a slug with a pattern, not an enum.
Recommended values go in the description. Adding a source is a data change, not
a schema change.

Generalised: enumerate values you control and that never grow. Everything else
gets a pattern.

---

## 5. Labels without names produce garbage

The domain taxonomy has codes `D01`–`D11`. The prompt listed the codes. Result:
almost every item landed in `D01`, because a model given an opaque list of
identifiers has no basis for choosing and defaults to the first.

Replacing `D01, D02, ... D11` with `D03=business, management, accounting...`
moved accounting-lecture items from `D01` to `D03` immediately, on the same
model, same temperature.

**Fix:** if a controlled vocabulary is going into a prompt, send the labels, not
just the identifiers. Store them next to the codes so they cannot drift —
`x-labels` in the schema, in this repo.

---

## 6. Timestamps that lie are worse than missing timestamps

An export format had no creation time. The obvious move was to fall back to the
filesystem mtime, which is always present.

That made every note look like it was written on the day of the export. Any
ordering by date became meaningless, and worse, it *looked* complete.

**Fix:** `created_at` is nullable, and there is a mandatory companion field
`created_at_source` with values `metadata | filename | filesystem | unknown`.
A guess is allowed only if it declares itself as a guess.

---

## 7. `verified_fact` is the label models over-apply

292 items came back tagged `verified_fact`. Sampling them found lecture
opinions, self-assessment, and a personal resolution about a test score. The
model was treating "stated confidently in the source" as "true".

**Fix:** a write-time guard, not a prompt instruction. `verified_fact` is
downgraded to `opinion` when the attribution is the author themselves, and to
`unverified_claim` when the content type cannot carry a checkable fact. The
downgrade is recorded in `processing_history` so it stays visible.

The general principle: if a model-tagged field has a safety meaning, enforce it
in code after generation. Prompts express intent; they do not enforce it.

---

## 8. What actually helped

Ranked by effect per unit of work:

1. **Classify material before extracting.** Removed 40% of the junk at the door.
2. **Extract from primary text, never from summaries.** Fixed attribution and
   made quotes real.
3. **Force a block citation on every item.** Turned "trust me" into "check line
   41".
4. **Send domain names, not codes.** One-line fix, immediate effect.
5. **Guard epistemic labels in code.** Removed an entire class of false
   confidence.
6. **Validate the relation graph.** Caught 206 fabricated edges.

What did *not* help: longer prompts, more keyword lists, higher temperature
sweeps. The failures were structural, not tuning problems.
