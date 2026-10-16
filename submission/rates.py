"""Rate questions: answered from structured rate rows, never from free text.

Pipeline (each step is one rule we can defend):
  1. intent       a rate word is present (rate, APR, APY, dividend, interest, ...)
  2. decline      the question names a product concept no rate row prices
                  ("adjustable-rate mortgage", "construction loan") -> "isn't listed"
  3. product      IDF-weighted overlap between question and each row's product text;
                  soft qualifiers the member did not ask for (jumbo, IRA, business,
                  intro, refinance, relief) push a row below any row without them
  4. authority    per product, the most authoritative tier that prices it (1 > 2 > 3);
                  tiers 4-6 (newsroom, blog, calculators, landing pages) never answer;
                  a table superseded by a newer copy of the same rows is stale
  5. hard         term, model year and balance/loan amount DROP rows that do not match;
                  nothing left -> decline (a neighbouring tier is a different fact)
  6. shape        superlative -> one row; otherwise every surviving row, one line each
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from authority import canonical_url
from parse import MONTHS, RateRow
from text import term_months, terms, tokenize

RATE_WORDS = terms("rate rates apr apy dividend dividends interest yield yields earn earns pay pays percent percentage")
# Words that say nothing about WHICH product ("what are your current rates right now").
FILLER = terms(
    "dupaco credit union cu current currently today now right latest good great better standard basic "
    "normal typical average general usual offer offers offered get give provide show see find want need only just "
    "did was were will one about around roughly approximately dollar dollars "
    "january february march april may june july august september october november december "
    "jan feb mar jun jul aug sep sept oct nov dec "
    "looking much tell know like charge charged available")
LIST_WORDS = terms("offer offers offered all list every types kinds options available")
HIGHEST = terms("highest best top most maximum max greatest")
LOWEST = terms("lowest cheapest minimum least smallest")
# Product head nouns: the word right before one of these names the product variant.
HEADS = terms("mortgage loan certificate account card checking savings line heloc ira moneymarket visa")

SOFT_FLAGS = {  # flag -> (words that mark a row, words in a question that ask for it)
    "jumbo": (terms("jumbo"), terms("jumbo")),
    "ira": (terms("ira roth traditional"), terms("ira roth traditional retirement")),
    "business": (terms("business"), terms("business company commercial")),
    "intro": (terms("intro introductory promo promotional"),
              terms("intro introductory promo promotional transfer")),
    "refinance": (terms("refinance refi"), terms("refinance refinancing refi cash-out")),
    "cashout": (terms("cash-out"), terms("cash-out")),  # a different product from a plain refinance
    "relief": (terms("relief"), terms("relief")),
}
MIN_OPEN_RE = re.compile(r"\bminimum (?:to open|deposit|opening|balance to open)|\bhow much (?:do i need )?to open\b", re.I)
NEGATION_RE = re.compile(r"\b(?:not|non|excluding|except|other than|rather than)[- ]+(?:the |a |an )?([a-z]+)", re.I)
PENALTY = 1000.0  # larger than any overlap score: an unrequested qualifier always loses
# A word on at most this many rows is a product NAME (Achieve, Bright, Launchpad), not a
# qualifier shared by many products (business, jumbo, regular).
DISTINCTIVE_DF = 10

TERM_TOKEN_RE = re.compile(r"^\d+mo$")
AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k\b|thousand)?|\b(\d{1,3}(?:,\d{3})+(?:\.\d+)?)\b|\b(\d+(?:\.\d+)?)\s?k\b", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
DATE_CONTEXT_RE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|november|december|as of|since|in|during|last)\s*$",
    re.I)


@dataclass
class _RowInfo:
    row: RateRow
    tier: int
    prod: frozenset          # product tokens: section + label, no numbers or rate words
    cooc: frozenset          # prod + URL path words, for "does any row price X Y?"
    flags: frozenset
    name: frozenset          # product name words: nearest heading + row label
    quals: tuple             # (term_months, amount_range, year_range)
    order: int
    borrowed: frozenset = frozenset()  # product words taken from the details page title


def _content(tokens) -> frozenset:
    # Numbers are qualifiers, not names; ordinals are names ("1st Rate Checking").
    return frozenset(t for t in tokens if t not in RATE_WORDS
                     and (not t[0].isdigit() or re.fullmatch(r"\d+(?:st|nd|rd|th)", t)))




# Words every heading uses; they say nothing about which product a row is.
NAME_GENERIC = terms("account accounts loan loans")


def same_fact(a: _RowInfo, b: _RowInfo) -> bool:
    """Two rows state the same fact when the tier qualifiers are identical and the
    product names mostly agree. Names are compared by overlap, not equality, because
    pages title the same table differently ("Auto & Motorcycle Loans" on /rates,
    "Auto Loan Rates" on the product page) while a different product with the same
    qualifiers ("ATV, Snowmobile & Jet Ski" vs "Auto", both "Model Year, 60 Months")
    shares almost no name words."""
    if a.quals != b.quals or a.flags != b.flags:  # a refinance row is never the purchase row
        return False
    union = a.name | b.name
    return bool(union) and len(a.name & b.name) / len(union) > 0.5  # {saving} vs {share, saving} is not enough


class RateIndex:
    """Per-snapshot view of the rate rows, built lazily on the first rate question."""

    def __init__(self, store):
        self.store = store
        self.infos: list[_RowInfo] = []
        for i, r in enumerate(store.rows):
            page = store.pages[r.url]
            if page.tier > 3:
                continue  # newsroom/blog/calculator/landing copies only ever feed conflicts()
            # The table's own "See details"/"Explore" link names its product page
            # (/personal/savings/certificates/), which matters when the headings are bare
            # ("Regular" > "12 Month" in the old layout). Last path segment only, so the
            # site-wide "/personal/" does not make every row look alike.
            details = urlparse(r.details_url or "").path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
            prod = _content(tokenize(" ".join(r.section) + " " + r.label + " " + details))
            # The nearest heading + label name the product. Flags come from these and the
            # URL only, never from an inherited group heading, so a heading cannot leak a
            # qualifier into the next table.
            name = _content(tokenize((r.section[-1] if r.section else "") + " " + r.label)) - NAME_GENERIC
            path_words = _content(tokenize(urlparse(r.url).path.replace("/", " ").replace("-", " ")))
            flags = frozenset(f for f, (marks, _) in SOFT_FLAGS.items() if (name | path_words) & marks)
            self.infos.append(_RowInfo(r, page.tier, prod, prod | path_words, flags, name,
                                       (r.term_months, r.amount_range, r.year_range), i))
        df = defaultdict(int)
        for info in self.infos:
            for t in info.prod:
                df[t] += 1
        # The title of a row's "See details" page is the site's own name for the product
        # (Bright Savings -> "Online Savings Account"). Only DISTINCTIVE title words are
        # added (on few rows), so a shared details page cannot make certificates look like
        # "savings".
        title_words = {}
        receivers = defaultdict(int)  # how many rows would GET each title word
        for info in self.infos:
            page = store.pages.get(canonical_url(info.row.details_url)) if info.row.details_url else None
            if page:
                title_words[id(info)] = _content(tokenize(page.title)) - FILLER - NAME_GENERIC
                for t in title_words[id(info)] - info.prod:
                    receivers[t] += 1
        for info in self.infos:
            extra = {t for t in title_words.get(id(info), ())
                     if df.get(t, 0) + receivers[t] <= DISTINCTIVE_DF}
            info.borrowed = frozenset(extra - info.prod)  # from the details title, not the row itself
            info.prod = info.prod | extra
            info.cooc = info.cooc | extra
        df = defaultdict(int)
        for info in self.infos:
            for t in info.prod:
                df[t] += 1
        self.df = df
        n = len(self.infos) or 1
        self.idf = {t: math.log(1 + n / d) for t, d in df.items()}
        self.all_prod = frozenset(df)
        self.stale_tables = self._stale_tables()

    def _stale_tables(self) -> set:
        """A table is stale if any of its rows is restated elsewhere with a NEWER
        effective date and a different value. What is out of date for the rows we can
        check is treated as out of date for the rows we cannot."""
        by_quals = defaultdict(list)
        for info in self.infos:
            if info.row.effective:
                by_quals[info.quals].append(info)
        stale = set()
        for group in by_quals.values():
            for a in group:
                for b in group:
                    if (b.row.effective > a.row.effective and b.row.metrics != a.row.metrics
                            and same_fact(a, b)):
                        stale.add((a.row.url, a.row.table))
                        break
        return stale


# "pay"/"earn" ask for a rate only in the shape "what does X pay" / "how much will X earn";
# otherwise they are ordinary words ("Apple Pay", "pay my loan", "earn rewards").
PAY_WORDS = terms("pay pays earn earns")
PAY_ASK_RE = re.compile(r"\b(?:what|how much)\b.*\b(?:pay|pays|earn|earns)\b(?!\s+(?:my|a|the|for|off|with|bills?))", re.I)


# The member explicitly asks for a rate ("what is your 5/1 ARM rate", "APR on a ...").
# Such a question is answered from rate rows or declined; never from a passage.
RATE_ASK_RE = re.compile(
    r"\bwhat(?:'s| is| are| was| were)\b[^?]*\b(?:rates?|aprs?|apys?|dividends?|yields?)\b"
    r"|\b(?:rates?|apr|apy)\s+(?:on|for)\b", re.I)


def is_rate_question(question: str) -> bool:
    q = set(tokenize(question))
    return (bool(q & (RATE_WORDS - PAY_WORDS)) or bool(q & PAY_WORDS and PAY_ASK_RE.search(question))
            or bool(MIN_OPEN_RE.search(question)))


def _question_amount(question: str) -> Optional[tuple[float, float]]:
    vals = []
    for m in AMOUNT_RE.finditer(question):
        raw = m.group(1) or m.group(3) or m.group(4)
        v = float(raw.replace(",", ""))
        if m.group(2) or (m.group(4) is not None):
            v *= 1000
        vals.append(v)
    # "fifteen thousand dollars", "a thirty-thousand-dollar loan"
    for m in WORD_THOUSAND_RE.finditer(question):
        n = words_to_number(m.group(1))
        if n:
            vals.append(n * 1000.0)
    # "12 thousand", "a balance of 12k" without a dollar sign.
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(?:thousand|grand)\b", question, re.I):
        vals.append(float(m.group(1)) * 1000)
    if not vals:
        return None
    return (min(vals), max(vals))


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
WORD_THOUSAND_RE = re.compile(
    r"\b((?:(?:" + "|".join(sorted(list(_UNITS) + list(_TENS) + ["hundred", "and"], key=len, reverse=True))
    + r")[\s-]*)+)[\s-]*thousand\b", re.I)


def words_to_number(text: str) -> int:
    """"two hundred fifty" -> 250, "thirty" -> 30 (enough for amounts said in words)."""
    total = 0
    for w in re.split(r"[\s-]+", text.lower()):
        if w in _UNITS:
            total += _UNITS[w]
        elif w in _TENS:
            total += _TENS[w]
        elif w == "hundred":
            total = max(total, 1) * 100
    return total


def _question_year(question: str) -> Optional[int]:
    for m in YEAR_RE.finditer(question):
        if not DATE_CONTEXT_RE.search(question[:m.start()]):
            return int(m.group(1))
    return None


def _unpriced_concept(q_tokens: list[str], idx: RateIndex) -> Optional[str]:
    """The word right before a product noun names the variant ("construction loan",
    "adjustable rate mortgage"). If the site uses that word but no rate row prices the
    variant, the honest answer is that the rate is not listed."""
    vocab = idx.store.index.postings
    for i, tok in enumerate(q_tokens):
        if tok not in HEADS:
            continue
        for prev in reversed(q_tokens[max(0, i - 3):i]):
            if prev in RATE_WORDS or prev[0].isdigit() or prev in HEADS:
                continue  # "7/1 adjustable-RATE mortgage", "30-year mortgage", "savings account"
            if prev in FILLER or prev not in vocab:
                break     # "current checking", or a word the site never uses (typo)
            if 0 < idx.df.get(prev, 0) <= DISTINCTIVE_DF:
                break     # a product NAME ("Achieve money market"): the member used another noun
            if not any(prev in info.cooc and tok in info.cooc for info in idx.infos):
                return f"{prev} {tok}"
            break
    return None


def _fmt_date(iso: str) -> str:
    y, m, d = iso.split("-")
    month = [k for k, v in MONTHS.items() if v == int(m)][0].title()
    return f"{month} {int(d)}, {y}"


def answer_rate(store, question: str, idx: RateIndex):
    """Returns (text, rows, confident) or None when no rate row is about this question."""
    # "regular, not jumbo", "non-jumbo", "the dividend rate (not the APY)": a negated word
    # is something the member does NOT want, so it must not count as asked for.
    negated = set()
    for m in NEGATION_RE.finditer(question):
        negated |= set(tokenize(m.group(1)))
    q_tokens = [t for t in tokenize(NEGATION_RE.sub(" ", question))]
    q = set(q_tokens)
    concept = _unpriced_concept(q_tokens, idx)
    if concept:
        return (f"A rate for that product ({concept}) isn't listed on the published rates. "
                "Please contact the credit union for a quote.", [], False)

    listing = bool(q & LIST_WORDS)
    asked = {f for f, (_, words) in SOFT_FLAGS.items() if q & words and not words & negated}
    wanted = q - RATE_WORDS - FILLER

    live = [i for i in idx.infos if (i.row.url, i.row.table) not in idx.stale_tables]
    overlap = {id(i): sum(idx.idf.get(t, 0.0) for t in wanted & i.prod) for i in live}
    if not live or max(overlap.values()) <= 0:
        return None  # nothing in the question names a product we price

    def score(info: _RowInfo) -> float:
        s = overlap[id(info)]
        if not listing:
            s -= PENALTY * len(info.flags - asked)
        return s

    terms_asked = set(term_months(question))
    year = _question_year(question)
    amount = _question_amount(question)

    # A term filters only products that are priced by term: "after the 6-month intro" on a
    # money market describes the promotion, not a 6-month product.
    termed = {i.name for i in live if i.row.term_months}

    def fits(i: _RowInfo) -> bool:
        r = i.row
        return ((not terms_asked or i.name not in termed or r.term_months in terms_asked)
                and (year is None or r.year_range is None or r.year_range[0] <= year <= r.year_range[1])
                and (amount is None or r.amount_range is None
                     or (r.amount_range[0] - 0.01 <= amount[0] and amount[1] <= r.amount_range[1] + 0.01)))

    # Hard constraints are applied BEFORE choosing the product: the product meant is the
    # best match that can price what was asked ("$250,000 30-year ... loan" is a mortgage,
    # not the home-improvement loan that happens to match "home" and "loan"). It must still
    # be a real match (half the best overall), so "84-month auto, 2019 car" declines
    # instead of jumping to another product.
    scored = [(score(i), i) for i in live if overlap[id(i)] > 0]
    fitting = [(s, i) for s, i in scored if fits(i)]
    # The product meant must keep the question's most specific product word: "48-month auto
    # on a 2022 car" must not become an ATV loan, "$8,000 boat loan" must not either.
    # The key word comes from the best match AFTER qualifier penalties (how the product is
    # chosen), so an unrequested intro/jumbo row cannot define the product.
    best_scored = max(sc for sc, _ in scored)
    key_words = {t for sc, i in scored if sc >= best_scored - 1e-9 for t in wanted & i.prod}
    key = max(sorted(key_words), key=lambda t: idx.idf.get(t, 0.0), default=None)
    if key:
        fitting = [(s, i) for s, i in fitting if key in i.prod - i.borrowed]
    top_ref = max(overlap[id(i)] for sc, i in scored if sc >= best_scored - 1e-9)
    if not fitting or max(overlap[id(i)] for _, i in fitting) < 0.5 * top_ref:
        return ("That combination isn't listed on the published rates. Please contact the credit union for details.",
                [], False)
    best = max(s for s, _ in fitting)
    family = [i for s, i in fitting if s >= best - 1e-9]

    # Per product, the most authoritative tier that prices it: a row is dropped when the
    # same fact is also stated by a more authoritative page.
    family = [i for i in family if not any(j.tier < i.tier and same_fact(i, j) for j in family)]

    rows = family
    if amount is not None:
        fits_amt = rows
        # Cumulative tiers ("$200,000 or less", "$300,000 or less"): keep the narrowest that fits.
        def width(i):
            return i.row.amount_range[1] - i.row.amount_range[0] if i.row.amount_range else math.inf
        rows = [i for i in fits_amt if not any(
            width(j) < width(i) and j.name == i.name and j.quals[0] == i.quals[0] for j in fits_amt)]
    if not rows:
        return ("That combination isn't listed on the published rates. Please contact the credit union for details.",
                [], False)

    # One line per distinct fact (the same row on /rates and its product page is one fact).
    unique = []
    for i in sorted(rows, key=lambda i: (i.tier, i.order)):
        if not any(same_fact(i, j) and i.row.metrics == j.row.metrics for j in unique):
            unique.append(i)
    # Within one product and term, list loan-amount tiers smallest first (the page order
    # varies by layout; ascending is stable across snapshots and reads naturally).
    first_seen = {}
    for n, i in enumerate(unique):
        first_seen.setdefault((i.name, i.quals[0]), n)
    unique.sort(key=lambda i: (first_seen[(i.name, i.quals[0])],
                               i.row.amount_range[1] if i.row.amount_range else 0))

    if q & (HIGHEST | LOWEST):
        def metric(i):
            m = i.row.metrics
            for k in ("apy", "apr", "rate", "dividend"):
                if k in m:
                    return m[k][1] if q & HIGHEST else m[k][0]
            return 0.0
        is_loan = any("apr" in i.row.metrics or "rate" in i.row.metrics for i in unique)
        want_high = bool(q & HIGHEST) and not (is_loan and "best" in q)  # "best" loan = lowest APR
        pick = (max if want_high else min)(unique, key=lambda i: (metric(i), -i.order))
        unique = [pick]

    unique = unique[:12]
    if MIN_OPEN_RE.search(question):
        # "Minimum to open" is a column of the same table: answer it, not the rate.
        mins = [(i.row.label or (i.row.section[-1] if i.row.section else ""), i.row.cells.get("min_open"))
                for i in unique if i.row.cells.get("min_open")]
        if mins:
            distinct = list(dict.fromkeys(m for _, m in mins))
            name = mins[0][0].split("(")[0].strip()
            return (f"{name}: minimum to open {', '.join(distinct)}.", [i.row for i in unique[:1]], True)
    lines = [i.row.text for i in unique]
    dates = {i.row.effective for i in unique if i.row.effective}
    text = lines[0] + "." if len(lines) == 1 else "Rates:\n" + "\n".join(f"- {l}" for l in lines)
    if len(dates) == 1:
        text += f"\nRates effective {_fmt_date(dates.pop())}."
    return (text, [i.row for i in unique], True)
