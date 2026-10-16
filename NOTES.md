# NOTES: a knowledge system that knows what's true now

## Running it

The website snapshots are not in this repo (they are the credit union's data, shared
for this event only). Copy the kit's `snapshots_zip/` folder into the repo root, then:

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
python harness/run_eval.py --submission submission --snapshots harness/selftest/snapshots \
    --eval harness/selftest/eval.jsonl --conflicts-gold harness/selftest/conflicts_gold.json
python harness/run_eval.py --submission submission --eval tests/question_bank.jsonl   # my own 141 questions
python -m unittest discover tests
```

- **API keys: none.** No network calls, no LLM, no third-party packages (Python 3.11 standard library only).
- Ingest of all five snapshots takes about 25 seconds. p95 answer time is about 0.5 seconds.
- `python tools/inspect_store.py --help` shows what was parsed (rate rows, hours, search hits).

## Approach in one paragraph

Everything that is expensive or judgement-based happens once, at ingest, and is kept per
snapshot. Each snapshot becomes a store of typed facts: rate rows parsed from tables (with
term, model-year range, balance tier, effective date and the "See details" product page),
branch hours and addresses, links, definitions, and text passages indexed with BM25.
`answer()` picks the snapshot, routes the question to one handler (links, branches,
routing/phone/who-is, rates, how-to, general facts and counts) and returns one value set
with its sources. The system is deterministic, so the same question always gets the same
answer, and every output can be traced to a rule.

I chose rules over an LLM on purpose. Most of the score is numeric (one current value, no
stale value anywhere in the answer, the same numbers across paraphrases, under 2 seconds).
Determinism wins all four, and a reviewer can run it without keys.

## How the system decides what is current

1. **Which snapshot.** `as_of` selects the latest snapshot taken on or before that date
   (dates before the first crawl use the first). Without `as_of`, an explicit phrase in the
   question ("in May 2026", "as of 2026-05-31") does the same; other dates in a question are
   content, not scope.
2. **Page authority.** Official rate sheets (`/rates`, `/business-rates`) > product pages and
   official PDFs > other official pages > newsroom > blog > calculators, landing pages and
   campaigns. Tag/author listings, tracking duplicates and spreadsheets are not indexed.
3. **Effective dates beat page type.** If the same fact is stated with an older "Rates are
   effective as of" date than another copy, the older table is stale. This is how calculator
   and landing-page copies lose, and it also caught the rate sheet itself lagging a product
   page once (business 17-month jumbo, Aug 2026).
4. **Expired offers are dropped.** A page marked expired, or stating an offer end date before
   the snapshot ("offer ended 2025-09-30"), never answers; its values only feed `conflicts()`.
5. **Rates come only from rate rows.** Newsroom, blog, calculator and landing-page numbers
   never answer a rate question.
6. **Dated statements.** For counts about the credit union itself ("22 branch office
   locations"), the newest dated statement wins. Year-tagged statistics ("294 employees used
   VTO in 2023") are answered by year, and a running count ("the fifth time Forbes
   recognized Dupaco") takes the newest statement.
7. **Pages missing from a later snapshot** are carried forward but demoted: they never answer
   rates or identifiers, rank at half weight, and the answer says the page was not in the
   latest crawl. This keeps partial crawls safe without letting a deleted page beat a live one.

## Getting the tier right

- Term, model year and balance or loan amount are **hard** constraints: a row that does not
  match is a different fact and is dropped. They are applied before choosing the product, so
  "$250,000 30-year mortgage" picks the mortgage tier that fits.
- Model years are parsed to number ranges, because the labels move between snapshots
  ("2023-26" became "2024-27").
- Jumbo, IRA, business, intro, refinance and similar are **soft**: a row with a qualifier the
  member did not ask for loses to one without it.
- Without a tier, all tiers of the product are listed (mortgages by loan amount, certificates
  by term); "highest/lowest" returns one row.
- One tokenizer is shared by the index, the questions and every keyword list, so "five-year
  CD", "60-month certificate" and "60 Months" are the same token, and paraphrases reach the
  same rows.

## When it declines

The answer says "isn't listed" (with `confident=False` and no extra numbers) when:

- the question names a product variant the site talks about but does not price ("7/1 ARM",
  "construction loan", "business loan rate");
- a hard constraint has no row (96-month auto loan, 84 months on a 2019 car);
- an explicit rate request matches no rate row;
- "do you offer X" has no official page as evidence;
- a named branch is unknown, or its hours/phone are not published (it never borrows another
  branch's data);
- a passage covers less than half of the question.

It never asks for a ZIP code, member number or authentication.

## conflicts()

One entry per fact, with all values oldest first, sources, the winner and a reason. Facts
are keyed on parsed numbers (product name, qualifiers, term, exact balance tier, overlapping
model years), so a page redesign or a different tier never looks like a contradiction. Types:

- rate sheet changed over time, and/or a stale copy elsewhere (calculator, landing page,
  embedded table) with an older effective date;
- expired promotion pages stating a different value;
- counts repeated in boilerplate across 3+ pages ("19 / 20 / 22 / 23 / 24 branch office
  locations" -> 22);
- running counts ("fourth time" vs "fifth time" -> fifth).

The winner is always what `answer()` returns. Not listed: tier or metric differences,
year-tagged statistics, and the Roth vs Traditional difference on one sheet (it may be
intentional). 53 entries on the five snapshots.

## Results

| Run | Score |
|---|---|
| Public practice set (40) | 0.969 (every scorable question; dup-005 has no `must_include`, so it cannot score) |
| Kit selftest (different site) | 1.0, conflicts F1 1.0 |
| My question bank (141: rewordings, as_of per snapshot, refusals, fees, counts, links) | 0.96, 0 stale-trap hits, paraphrase consistency 0.93 |

## Known limits

- Hours exist only in 2021-2024 branch-opening press releases; the answer says so.
- Lexical matching: "losing the key" does not match "lost key"; a few link and
  eligibility questions pick a near-miss page.
- The rules were shaped on this one site. Every rule is general (no answers, values or URLs
  from the practice set are hard-coded), but new page layouts could need new parsing.
- `conflicts()` may over-list "changed over time" entries if the hidden list only counts
  same-moment contradictions.

## AI assistance

I used an AI coding assistant (Claude Code) throughout, phase by phase: it explored the
data with me, proposed options with trade-offs, wrote code after I agreed on each rule, and
ran the evaluations. I reviewed every design decision and can explain each rule above.
