# Sample corpus

Four synthetic files, written for this repository. No real user data.

| File | Shape | Why it is here |
|---|---|---|
| `esp32-power-budget.md` | technical note | dense, factual, extracts cleanly |
| `accounting-lecture.srt` | two-speaker subtitles | tests speaker + timestamp handling and attribution |
| `migration-retro.vtt` | WebVTT | tests a second subtitle flavour, short cues |
| `tuesday-journal.md` | diary | negative case: most of it should **not** become knowledge |

`sample_output/` holds two items and three edges produced by running the
pipeline over this corpus, so the output format is visible without an API key.

Regenerate:

```bash
pks parse examples/corpus -o out/parsed
pks extract out/parsed -o out/items      # needs PKS_API_KEY
pks relate out/items -o out/relations
```
