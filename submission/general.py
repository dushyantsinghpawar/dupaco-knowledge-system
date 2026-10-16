"""General facts from passages, and "how many" counts.

  G1 candidates   top BM25 passages; never expired pages or tier 6 (calculators,
                  landing pages, campaigns)
  G2 re-rank      score x authority weight (official 1.0, newsroom 0.85, editorial 0.7);
                  within 10% of the best, the newer page wins
  G3 coverage     the passage must hold >= 50% of the question's IDF weight, else
                  "isn't listed" (no guessing from a loosely related page)
  G4 output       the passage's sentences that contain question terms, quoted as written
  G5 counts       "how many X": "N <X>" statements from tiers 1-4.
                    no year attached  -> current state: newest dated statement wins
                    year attached     -> a year-labelled series, newest first; a year
                                         named in the question selects that year only
  G6 ordinals     "the fifth time ..." is a running count: newest dated statement wins
  G7 definitions  a phrase the answer relies on ("charter area") that an official page
                  defines as a labelled list is appended from that page
"""
from __future__ import annotations

import re

from store import page_date
from text import STOPWORDS, terms, tokenize

# Product nouns (same list the rate engine uses): a question naming one needs a passage
# about that kind of product, not merely about the same people or topic.
PRODUCT_NOUNS = terms("loan loans mortgage certificate account card checking savings heloc ira moneymarket visa")

WEIGHT = {1: 1.0, 2: 1.0, 3: 1.0, 4: 0.85, 5: 0.7}
NEAR_TIE = 0.10
MIN_COVERAGE = 0.5
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“-])|\s+-\s+(?=[A-Z*])")
OFFER_RE = re.compile(r"\b(?:do|does)\s+(?:you|dupaco|the credit union)\s+(?:offer|have|provide|sell|support)\b|\bcan i (?:get|open)\b", re.I)
# "how many X", or a question that starts with "(the total) number of X" ("phone number of" is not one).
# Questions that ask for an amount. Not "fee"/"charge": "Does X charge a fee?" is yes/no.
AMOUNT_Q_RE = re.compile(r"\bhow much\b|\bcosts?\b|\bminimum\b|\bprice\b", re.I)
HOW_MANY_RE = re.compile(r"\bhow many\b|^\s*(?:what(?:'s| is) )?(?:the )?(?:total )?number of\b", re.I)
# Boilerplate about the credit union itself ("It has over 600 employees and 22 branch
# office locations", "Dupaco serves residents in 118 counties").
SELF_RE = re.compile(r"^(?:It|Dupaco(?: Community Credit Union)?|The credit union)\b|\bDupaco (?:has|serves|is|operates|employs)\b")
# A count is a standalone number: not a money amount, percent, or part of a phone number.
NUMBER_RE = re.compile(r"(?<![\d.$-])(\d{1,3}(?:,\d{3})+|\d+)(?![\d.,]*%|-\d|\.\d)")
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
ORDINAL_RE = re.compile(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)) "
                        r"(?:time|consecutive year|straight year|year in a row)", re.I)
COUNT_SKIP = terms("dupaco credit union cu community does do have has are is there the your total")
SCOPE_WORDS = terms("january february march april may june july august september october november december")
DEF_STOP = STOPWORDS | {"follows", "following", "include", "includes", "from", "have", "want", "need", "handy"}


def running_ordinal(text: str) -> list[tuple[str, str, str]]:
    """(ordinal phrase, subject, verb) for running counts such as "the fifth time Forbes
    recognized Dupaco". The named subject must follow directly: "for the first time in a
    long time" is an idiom, not a count."""
    out = []
    for m in ORDINAL_RE.finditer(text):
        sm = re.match(r"\s+(?:that\s+)?([A-Z][a-z]+)\s+(?:has\s+|have\s+)?([a-z]+)", text[m.end():])
        if sm and sm.group(1) != "Dupaco":
            out.append((m.group(0), sm.group(1), sm.group(2)))
    return out


def _sentences(text: str) -> list[str]:
    return [s.strip(" -") for s in SENTENCE_RE.split(text) if s.strip(" -")]


def _date(store, url: str) -> str:
    return page_date(store, url)


class GeneralIndex:
    def __init__(self, store):
        self.store = store
        self.defs: dict[str, tuple[str, str]] = {}
        for phrase, text, url in store.definitions:
            words = phrase.split()
            if all(len(w) >= 4 and w not in DEF_STOP for w in words):
                self.defs.setdefault(phrase, (text, url))

    # ---------------------------------------------------------------- passages

    def _ranked(self, question: str):
        st = self.store
        # Scope words ("in January 2026") chose the snapshot; they are not part of the topic.
        q = {t for t in tokenize(question) if t not in SCOPE_WORDS and not YEAR_RE.fullmatch(t)}
        total = sum(st.index.idf(t) for t in q) or 1.0
        out = []
        for i, score in st.index.search(question, k=40):
            p = st.passages[i]
            page = st.pages[p.url]
            if page.tier > 5 or page.expired:
                continue
            ptoks = set(tokenize(p.heading + " " + p.text))
            coverage = sum(st.index.idf(t) for t in q & ptoks) / total
            weight = WEIGHT[page.tier] * (0.5 if page.carried_from else 1.0)  # fallback only
            # Coverage scales the score: a passage answering MORE of the question beats a
            # one-line row that merely matches a few words tightly.
            out.append((score * weight * coverage, coverage, _date(st, p.url), i))
        return out

    def passage_answer(self, question: str):
        st = self.store
        q = set(tokenize(question))
        all_ranked = self._ranked(question)
        need = q & PRODUCT_NOUNS
        if need:
            all_ranked = [r for r in all_ranked
                          if need <= set(tokenize(st.passages[r[3]].heading + " " + st.passages[r[3]].text))]
        if OFFER_RE.search(question):
            # Whether Dupaco offers something is shown by its own product/official pages;
            # a news or blog mention (an IRS-scam warning naming cryptocurrency) is not an offer.
            all_ranked = [r for r in all_ranked if st.pages[st.passages[r[3]].url].tier <= 3]
        ranked = [r for r in all_ranked if r[1] >= MIN_COVERAGE]
        if AMOUNT_Q_RE.search(question):
            # "How much / cost / fee / minimum" wants an amount: prefer passages stating one.
            with_amount = [r for r in ranked if re.search(r"\$\s?\d|\d\s?%", st.passages[r[3]].text)]
            ranked = with_amount or ranked
        if not ranked:
            return ("That isn't listed on the website. Please contact the credit union for help.", [], False)

        # G6: running counts ("the fifth time Forbes ...") - the newest statement wins.
        with_ord = [r for r in all_ranked if running_ordinal(st.passages[r[3]].text)]
        present = set().union(*(set(tokenize(st.passages[r[3]].text)) for r in with_ord)) if with_ord else set()
        # The subject is the question term most of those statements share ("Forbes"),
        # not merely the rarest word in the question.
        def n_ord(t):
            return sum(t in tokenize(st.passages[r[3]].text) for r in with_ord)
        subject = max(sorted(q & present - COUNT_SKIP), key=lambda t: (n_ord(t), st.index.idf(t)), default=None)
        ordinal = [r for r in with_ord if subject and subject in tokenize(st.passages[r[3]].text)]
        if ordinal:
            # Quote only the running-count sentences of the newest page that states them:
            # the rest of a press release carries other dates and numbers.
            newest = max(r[2] for r in ordinal)
            url = max((st.passages[r[3]].url for r in ordinal if r[2] == newest))
            sents = [x for r in sorted(ordinal, key=lambda r: r[3]) if st.passages[r[3]].url == url
                     for x in _sentences(st.passages[r[3]].text)
                     if running_ordinal(x) and subject in tokenize(x)]
            return " ".join(dict.fromkeys(sents)), [url], True

        top = max(r[0] for r in ranked)
        near = [r for r in ranked if r[0] >= top * (1 - NEAR_TIE)]
        best = max(near, key=lambda r: (r[2], r[0], -r[3]))
        p = st.passages[best[3]]
        # The best passage plus up to two other strong passages from the SAME page, quoted
        # in page order: one source, read the way it was written ("The Home$tart program
        # ..." + "up to $15,000"). The cap is high enough never to cut the best passage.
        others = [] if p.is_row else sorted(
            (r for r in all_ranked if r[3] != best[3] and st.passages[r[3]].url == p.url
             and not st.passages[r[3]].is_row and r[0] >= top * 0.5), key=lambda r: -r[0])[:2]
        chosen = sorted([best[3]] + [r[3] for r in others])
        sents = [x for i in chosen for x in _sentences(st.passages[i].text)]
        # Skip leading off-topic sentences, unless the heading already says the passage is
        # about the question ("You're Eligible to Join If ..." above a bullet list).
        on_topic = q & set(tokenize(p.heading))
        first = 0 if on_topic else next((k for k, x in enumerate(sents) if q & set(tokenize(x))), 0)
        text = " ".join(sents[first:])
        if len(text) > 1200:
            text = text[:1200].rsplit(" ", 1)[0] + "..."
        urls = [p.url]
        if st.pages[p.url].carried_from:
            text += (f" (This comes from a page that wasn't in the latest crawl of the site; "
                     f"last seen {st.pages[p.url].carried_from}.)")
        # G7: append the site's own definition of a phrase this answer relies on.
        low = text.lower()
        for phrase, (definition, url) in sorted(self.defs.items()):
            if phrase in low and url != p.url:
                text += f" ({phrase.capitalize()}: {definition}.)"
                urls.append(url)
                break
        return text, urls, True

    # ---------------------------------------------------------------- counts

    def count_answer(self, question: str, as_of_year: int):
        st = self.store
        q_toks = tokenize(question)
        low = question.lower()
        m = re.search(r"how many|number of", low)
        after = tokenize(low[m.end():]) if m else q_toks
        nouns = [t for t in after if t not in COUNT_SKIP]
        if not nouns:
            return None
        noun, context = nouns[0], set(nouns[1:])
        q_year = next((int(y) for y in YEAR_RE.findall(question)), None)
        last_year = bool(re.search(r"\blast year\b", low))

        cands = []
        for p in st.passages:
            page = st.pages[p.url]
            if page.tier > 4 or page.expired:
                continue
            if noun not in tokenize(p.text):
                continue
            for s in _sentences(p.text):
                toks = tokenize(s)
                for m in NUMBER_RE.finditer(s):
                    if YEAR_RE.fullmatch(m.group(1)):
                        continue
                    # "22 branch office locations": the noun follows closely, no punctuation
                    # between ("...7600 or visit a branch" and "60 days, visit a branch" fail).
                    gap = s[m.end():m.end() + 30]
                    head = re.split(r"[,.;:!?()]", gap)[0]
                    if noun not in tokenize(" ".join(head.split()[:3])):  # raw words, before stopword removal
                        continue
                    phrase = s[m.start():m.end() + 60]
                    phrase = re.split(r"\s(?:in|across|throughout|that|who|and|to|with|at)\s|[,.;:]", phrase)[0]
                    years = [int(y) for y in YEAR_RE.findall(s)]
                    cands.append({"n": m.group(1), "self": bool(SELF_RE.search(s)), "phrase": phrase.strip(), "year": years[0] if years else None,
                                  "match": len(context & set(toks)), "date": _date(st, p.url), "url": p.url})
        if not cands:
            return None
        best_match = max(c["match"] for c in cands)
        cands = [c for c in cands if c["match"] == best_match]

        tagged = [c for c in cands if c["year"]]
        if tagged and (q_year or last_year or len(tagged) >= len(cands) / 2):
            by_year = {}
            for c in sorted(tagged, key=lambda c: (c["year"], c["date"])):
                by_year[c["year"]] = c  # newest statement about each year
            if q_year:
                c = by_year.get(q_year)
                if not c:
                    return (f"The website doesn't report that figure for {q_year}.", [], False)
                return f"In {q_year}: {c['phrase']}.", [c["url"]], True
            years = sorted(by_year, reverse=True)[:3]
            lead = ""
            if last_year and years[0] != as_of_year - 1:
                lead = f"The site doesn't report a {as_of_year - 1} figure. Most recent: "
            parts = [f"{by_year[y]['phrase']} in {y}" for y in years]
            return lead + "; ".join(parts) + ".", [by_year[y]["url"] for y in years], True

        # A current-state count must be a statement about Dupaco itself; a stray "10 employees"
        # in an FAQ or a business article is not a claim about the credit union.
        current = ([c for c in cands if not c["year"] and c["self"]]
                   or [c for c in cands if not c["year"]] or cands)
        c = max(current, key=lambda c: (c["date"], c["url"]))
        return (f"{c['phrase']} (Dupaco's most recent statement, {c['date']}).", [c["url"]], True)


def answer_general(question: str, idx: GeneralIndex, as_of_year: int):
    # "How many times has Forbes ..." is a running count ("the fifth time"), not "N <noun>".
    if HOW_MANY_RE.search(question) and not re.search(r"\bhow many times\b", question, re.I):
        res = idx.count_answer(question, as_of_year)
        if res:
            return res
    return idx.passage_answer(question)
