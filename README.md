# Bid Desk

Score an incoming RFP, tender, or security questionnaire against your library of
past proposals, and get back the one thing a bid team actually needs first:
**what can we not currently answer?**

Extraction, retrieval, and scoring run entirely locally — no API key, no network
call, no per-run cost, and no dependencies outside the Python standard library.

```bash
python -m biddesk run --rfp samples/rfp/ITT-2026-041-managed-it.md --corpus samples/corpus
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

## Three ways to run it

| Command | For |
|---|---|
| `run` | Score one tender. The default — bare flags still work without naming it. |
| `watch` | Point it at a folder. Drop a tender in, a report appears. No terminal needed. |
| `draft` | Score, then draft answers where evidence exists. Needs an API key. |

## What it produces

Three views of one coverage matrix, ordered so the bid-losing items sit at the top:

| File | For |
|---|---|
| `coverage.html` | The client-facing report. Self-contained, prints cleanly, light and dark. |
| `coverage.csv` | The bid team's working file — filter, assign, track. UTF-8 with BOM so Excel reads it correctly. |
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

So this tool decides, per requirement, whether your own material supports an
answer, and it refuses to guess. Drafting is a separate, optional stage that runs
on the client's own credentials, over evidence this stage has already verified
exists — and **gaps are never drafted at all**.

That also makes the whole scoring pipeline deterministic: the same RFP and corpus
always produce the same matrix, and any verdict traces to the section that
produced it. You cannot audit a model's silent omission.

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

## Watch mode

The people who own bid responses are not the people who run terminals.

```bash
python -m biddesk watch --corpus ./past-proposals --inbox ./inbox --out ./reports
```

Drop a tender into `inbox`; the report appears in `reports` and the tender moves
to `inbox/done`. On Windows, edit the paths in `watch-folder.cmd` and double-click it.

Three things make it survive unattended use:

- Files are processed only once their size stops changing, so a tender still
  copying over the network is never read half-written.
- One unreadable file moves to `inbox/failed/` with the reason in a `.error.txt`
  beside it, and the loop keeps running. A watcher that dies on the first bad PDF
  is useless.
- The corpus is re-indexed when it changes on disk, so adding a past proposal
  takes effect without a restart.

## Drafting (optional)

```bash
pip install anthropic
python -m biddesk draft --rfp tender.pdf --corpus ./past-proposals --dry-run
```

`--dry-run` prints the exact prompts and sends nothing, so you can inspect what
would go to the API — and what it would cost — before spending anything.

Two mechanisms make this safe to sell, and both are tested offline in
`tests/test_draft.py`:

1. **Gaps are never drafted.** A requirement with no supporting evidence is never
   put in front of the model, so there is nothing to invent from. This is
   enforced by construction, not by asking the model nicely.
2. **The model can refuse.** Each draft carries `evidence_sufficient`. Where the
   retrieved sections do not actually support an answer, it says so and the
   refusal is reported, rather than a plausible answer being written.

A requirement the model drops from its response is reported as undrafted rather
than silently vanishing — a missing answer must never read as a finished one.

The client supplies their own API key (`ANTHROPIC_API_KEY`), so this costs you
nothing to ship or demo. The Anthropic SDK is the *only* dependency in the
project, and it is needed only for this command.

## Tests

```bash
python run_tests.py
```

Six suites, 100+ checks:

| Suite | Covers |
|---|---|
| `tests/evaluate.py` | Scoring quality against hand-labelled ground truth |
| `tests/test_docparse.py` | `.docx` / `.xlsx` readers, split runs, table rows |
| `tests/test_pdf.py` | PDF text extraction, string escaping, refusal cases |
| `tests/test_robustness.py` | Damaged files, empty inputs, CLI exit codes |
| `tests/test_draft.py` | Drafting safety — no network calls |
| `tests/test_watch.py` | Folder watch end to end, including recovery from a bad file |

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

`.pdf`, `.docx`, `.xlsx`, `.md`, `.txt` — all read through the standard library,
nothing to install.

Word tables and spreadsheet rows are flattened to `cell | cell` so a clause
reference stays attached to the requirement text beside it; questionnaire tenders
put those in separate cells, and reading paragraphs naively orphans every
reference. Word runs are joined, since Word splits sentences at any formatting
change.

**PDFs** are parsed from their raw content streams (Flate-decompressed with
`zlib`), which covers PDFs exported from Word — the overwhelming majority of
tenders. Two cases are refused rather than guessed at:

- **Scanned PDFs** hold page images, not text. They need OCR first.
- **Badly-encoded PDFs** extract to mojibake. That text would still produce
  "requirements", which would be scored and reported as gaps — a confidently
  wrong report. Garbled output is detected and the file is refused.

## Handling bad input

Clients send legacy `.doc` files renamed to `.docx`, zero-byte downloads, and
password-protected files. All fail with a message naming the file and the fix,
never a traceback.

Corpus files that cannot be read are **reported before the results, not silently
skipped**. A proposal document that fails to parse is missing evidence, so every
requirement it would have answered is reported as a gap — the report is then
confidently wrong in exactly the direction the client will act on.

```
  WARNING: 2 file(s) in the corpus could not be read.
  Requirements they would have answered will show as gaps.
    - capability-statement.docx: not a valid .docx file. This is usually a
      legacy .doc/.xls renamed to .docx...
    - archive.pdf: no extractable text found. This is usually a scanned PDF...
```

## Limitations

- Lexical matching only. A requirement phrased in entirely different vocabulary
  from your library reads as a gap. That errs in the safe direction — it
  over-reports gaps rather than under-reporting them — but it is real.
- Thresholds are calibrated on one 39-requirement sample. Treat the defaults as
  a starting point, not a finding.
- Requirement extraction is tuned to UK/EU public-sector tender conventions
  (numbered clauses, "shall"/"must"). Other formats may need rule changes.
- PDF extraction handles digitally-generated files, not scanned ones, and not
  every exotic font encoding. It refuses rather than guessing.
