# Bid Desk

Score an incoming RFP, tender, or security questionnaire against your library of
past proposals, and get back the one thing a bid team actually needs first:
**what can we not currently answer?**

Extraction, retrieval, and scoring run entirely locally. No API key, no network
call, no per-run cost.

```bash
python -m biddesk --rfp samples/rfp/ITT-2026-041-managed-it.md --corpus samples/corpus --out out
```

```
  RFP          ITT-2026-041-managed-it.md
  Corpus       19 sections from samples/corpus
  Extracted    39 requirements (39 binding, 0 questions)

  ANSWERED      15  (38%)
  WEAK          10
  GAP           14
  CRITICAL      14  binding, no evidence on file

  Written to   out/coverage.{html,md,csv}
```

## What it produces

Three views of one coverage matrix, ordered so the bid-losing items sit at the top:

| File | For |
|---|---|
| `coverage.html` | The client-facing report. Self-contained, prints cleanly, light and dark. |
| `coverage.csv` | The bid team's working file — filter, assign, track. |
| `coverage.md` | Drops into a wiki, ticket, or email. |

Every requirement carries a verdict:

- **ANSWERED** — the proposal library covers this; a human still approves it.
- **WEAK** — related material exists but does not clearly meet the requirement.
- **GAP** — nothing on file.
- **CRITICAL** — a *binding* obligation ("shall", "must") with no evidence. These
  are what lose bids, so they are listed first and flagged separately.

## The design decision that matters

The obvious build is to pipe the RFP into a language model and ask it to write
answers. That is the wrong product. A hallucinated answer in a tender is a
**contractually binding commitment to a capability you do not have** — and it is
invisible, because it reads exactly like the answers around it.

So this tool does not write answers. It decides, per requirement, whether your
own material supports one, and it refuses to guess. Drafting is a separate,
optional step that runs on the client's own credentials, against evidence this
stage has already verified exists.

That also makes the whole scoring pipeline deterministic — the same RFP and
corpus always produce the same matrix, and any verdict can be traced to the
section that produced it. You cannot audit a model's silent omission.

## How scoring works

1. **Extract** requirements with rules, not a model. Numbered clauses, binding
   modals, questions, imperative asks ("Describe your..."), and table/spreadsheet
   rows. A model that silently drops a binding clause produces a gap report that
   is *worse than useless*, because it reports false confidence.
2. **Index** the proposal library into heading-level sections.
3. **Rank** candidate evidence with BM25.
4. **Score** coverage as the share of the requirement's distinctive vocabulary
   present in the top 3 pooled sections, weighted by **idf squared**.

Two details in step 4 came out of fixing real false negatives, and both are
worth knowing if you tune this:

- **Squared idf.** With linear weighting, a near-conclusive rare term ("TUPE",
  "CREST", "BPSS") counts as one term among ten, so requirements whose defining
  word was sitting in the corpus still scored as gaps. Squaring widens the rare/
  generic ratio from ~4x to ~16x, which is what makes the decisive word decisive.
- **Capped weight for unseen terms.** Terms absent from the entire corpus are
  weighted at the rarest *observed* term, not the theoretical maximum. Uncapped,
  incidental contract phrasing ("bear", "accordance", "associated") outweighed
  the actual domain term and manufactured gaps.

Coverage stays bounded 0–1 throughout, so thresholds are meaningful and the
number explains to a client as "share of this requirement's distinctive
vocabulary we can evidence."

## Calibration

Thresholds default to `--weak-at 0.42` and `--answered-at 0.62`, calibrated on
`samples/` where covered requirements scored 0.45–1.00 and real gaps 0.17–0.49.

`ANSWERED_AT` sits well clear of the highest observed gap because the only
unforgivable error is a gap marked ANSWERED — it never reaches a human. The
GAP/WEAK boundary is deliberately looser: both route to review.

**These are corpus-dependent.** A library with different house vocabulary shifts
the distribution. Recalibrate per client by labelling a sample of their own
material and running `tests/evaluate.py`.

## Tests

```bash
python tests/evaluate.py      # scoring quality vs. hand-labelled ground truth
python tests/test_docparse.py # .docx / .xlsx readers
```

`evaluate.py` scores the two error classes separately, because they are not
equally bad:

```
  Gap recall           13/15 (87%) real gaps flagged as GAP
  Covered recall       23/23 (100%) covered items not lost to false gaps

  FALSE CONFIDENCE     0
  FALSE GAPS           0
```

A **false confidence** (real gap marked ANSWERED) fails the suite outright. A
**false gap** wastes reviewer time and is tolerated up to 2.

## Input formats

`.docx`, `.xlsx`, `.md`, `.txt` — read through the standard library only, no
dependencies to install.

Word tables and spreadsheet rows are flattened to `cell | cell` so a clause
reference stays attached to the requirement text beside it; questionnaire
tenders put those in separate cells and reading paragraphs naively orphans every
reference. Word runs are joined, since Word splits sentences at any formatting
change.

PDFs need converting to `.docx` or `.txt` first.

## Limitations

- Lexical matching only. A requirement phrased in entirely different vocabulary
  from your library will read as a gap. That failure is in the safe direction —
  it over-reports gaps rather than under-reporting them — but it is real.
- Thresholds are calibrated on one 39-requirement sample. Treat the defaults as
  a starting point, not a finding.
- Requirement extraction is tuned to UK/EU public-sector tender conventions
  (numbered clauses, "shall"/"must"). Other formats may need rule changes.
- No drafting step is implemented yet; the matrix is the deliverable.
