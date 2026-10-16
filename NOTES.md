# NOTES: a knowledge system that knows what's true now

## How to run it

The website snapshots aren't in this repo. They're the credit union's data and were only shared for this event. Copy the kit's `snapshots_zip/` folder into the repo root, then run:

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
python harness/run_eval.py --submission submission --snapshots harness/selftest/snapshots \
    --eval harness/selftest/eval.jsonl --conflicts-gold harness/selftest/conflicts_gold.json
python harness/run_eval.py --submission submission --eval tests/question_bank.jsonl     # my own 141 questions
python harness/run_eval.py --submission submission --eval tests/blind_bank.jsonl        # 106 written without seeing the code
python harness/run_eval.py --submission submission --eval tests/paraphrase_bank.jsonl   # 70 questions x 4 wordings
python -m unittest discover tests
```

- **No API keys needed.** No network calls, no LLM, no third-party packages. It's Python 3.11 standard library only.
- Ingesting all five snapshots takes about 25 seconds. p95 answer time is about 0.6 seconds.
- `python tools/inspect_store.py --help` lets you see what got parsed (rate rows, hours, search hits).

## The approach

I do all the expensive and judgement-heavy work once, at ingest, and keep it per snapshot. Each snapshot turns into a store of typed facts:

- rate rows parsed from tables, with term, model-year range, balance tier, effective date and the "See details" product page
- branch hours and addresses
- links and definitions
- text passages indexed with BM25

When `answer()` gets a question, it picks the snapshot, sends the question to one handler (links, branches, routing/phone/who-is, rates, how-to, or general facts and counts) and returns one set of values with their sources. It's deterministic, so the same question always gets the same answer, and you can trace every output back to a rule.

I went with rules instead of an LLM on purpose. Most of the score is about numbers: one current value, no stale value anywhere in the answer, the same numbers across paraphrases, and under 2 seconds. Determinism handles all four, and anyone reviewing it can run it without keys.

## How it decides what's current

1. **Pick the snapshot.** `as_of` selects the latest snapshot taken on or before that date. If the date is before the first crawl, it uses the first one. If there's no `as_of`, an explicit phrase in the question like "in May 2026", "aug 2026" or "as of 2026-05-31" does the same thing. Any other date in the question is treated as content, not scope.
2. **Rank pages by authority.** Official rate sheets (`/rates`, `/business-rates`) come first, then product pages and official PDFs, then other official pages, then newsroom, then blog, then calculators, landing pages and campaigns. Tag and author listings, tracking duplicates and spreadsheets don't get indexed at all.
3. **Effective dates win over page type.** If two copies of the same fact exist and one has an older "Rates are effective as of" date, the older table is stale. That's how calculator and landing-page copies lose. It also caught the rate sheet itself lagging behind a product page once (business 17-month jumbo, Aug 2026).
4. **Expired offers are dropped.** If a page is marked expired, or says an offer ended before the snapshot date ("offer ended 2025-09-30"), it never answers anything. Its values only go into `conflicts()`.
5. **Rates only come from rate rows.** Numbers from the newsroom, blog, calculators or landing pages never answer a rate question.
6. **Dated statements.** For counts about the credit union itself ("22 branch office locations"), the newest dated statement wins. Stats tied to a year ("294 employees used VTO in 2023") get answered by year. For a running count ("the fifth time Forbes recognized Dupaco"), the newest statement wins. An undated document is dated by the year in its title, so the "2025 Impact Report" counts as 2025, not as today.
7. **Pages missing from a later snapshot** get carried forward but demoted. They never answer rates or identifiers, they rank at half weight, and the answer notes the page wasn't in the latest crawl. That keeps partial crawls safe without letting a deleted page beat a live one.

## Getting the tier right

- Term, model year and balance or loan amount are **hard** constraints. If a row doesn't match, it's a different fact, so it gets dropped. These are applied before picking the product, so "$250,000 30-year mortgage" lands on the mortgage tier that actually fits.
- Model years get parsed into number ranges, because the labels shift between snapshots ("2023-26" became "2024-27").
- Jumbo, IRA, business, intro, refinance and similar qualifiers are **soft**. If a row has a qualifier the member didn't ask for, it loses to one that doesn't.
- If no tier is given, it lists every tier of the product (mortgages by loan amount, certificates by term). "Highest" or "lowest" returns one row.
- The index, the questions and every keyword list all share one tokenizer. So "five-year CD", "60-month certificate" and "60 Months" become the same token, and paraphrases hit the same rows.

## When it says no

It answers "isn't listed" (with `confident=False` and no extra numbers) when:

- the question asks about a product variant the site mentions but doesn't price ("7/1 ARM", "construction loan", "business loan rate")
- a hard constraint has no matching row (96-month auto loan, 84 months on a 2019 car)
- someone explicitly asks for a rate and no rate row matches
- a "do you offer X" question has no official page backing it up
- a named branch is unknown, or its hours or phone aren't published (it never borrows another branch's data)
- a passage covers less than half of the question

It never asks for a ZIP code, member number or any kind of authentication. And if any handler ever throws an error, the answer becomes an honest "can't confirm" instead of a crash.

## conflicts()

There's one entry per fact, with every value listed oldest first, plus sources, the winner and a reason. Facts are keyed on parsed values (product name, qualifiers, term, exact balance tier, overlapping model years), so a page redesign or a different tier doesn't show up as a contradiction. The types it catches:

- the rate sheet changed over time, and/or a stale copy somewhere else (calculator, landing page, embedded table) has an older effective date
- expired promotion pages showing a different value
- a table labelled only with generic words ("Saving Account Rates", really the HSA at 0.35%) next to the named product on the same sheet (Share Savings at 0.20%)
- counts repeated in boilerplate across 3+ pages ("19 / 20 / 22 / 23 / 24 branch office locations" -> 22)
- running counts ("fourth time" vs "fifth time" -> fifth)

The winner is always what `answer()` returns. Contradictions that exist at the same moment are listed first, then values that only changed over time. Things I left out on purpose: tier or metric differences, year-tagged stats, and the Roth vs Traditional difference on one sheet, since that might be intentional. On the five snapshots there are 54 entries. Against a stand-in gold list built from the practice set's traps that really appear on the site (`tests/conflicts_proxy_gold.json`, used only for testing), all 9 match and every winner is right.

## Results

| Run | Score |
|---|---|
| Public practice set (40) | 0.969 (every scorable question; dup-005 has no `must_include`, so it can't be scored) |
| Kit selftest (different site) | 1.0, conflicts F1 1.0 |
| My question bank (141: rewordings, as_of per snapshot, refusals, fees, counts, links) | 0.97, 0 stale-trap hits, paraphrase consistency 0.93 |
| Blind set (106, written by a separate agent that only read the snapshots) | 0.91, 0 stale-trap hits (0.77 on first run, before fixing the general patterns it exposed) |
| Paraphrase set (70 questions x 4 wordings) | 0.95, 0 stale-trap hits, paraphrase consistency 0.91 |

## Known limits

- Branch hours only show up in the 2021-2024 branch-opening press releases, and the answer says that.
- Matching is lexical. "Losing the key" won't match "lost key", and a few link and eligibility questions land on a page that's close but not right.
- I shaped the rules on this one site. Every rule is general (nothing from the practice set is hard-coded, no answers, values or URLs), but a new page layout could need new parsing.
- `conflicts()` might over-list "changed over time" entries if the hidden list only counts contradictions that exist at the same moment.

## AI assistance

I used Claude Code throughout, one phase at a time. It explored the data with me, laid out options with trade-offs, wrote code once I'd agreed on each rule, and ran the evaluations. I reviewed every design decision and can explain each rule above.