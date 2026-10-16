"""The ONE tokenizer, shared by the BM25 index, the questions and every keyword list.

Members and the site word the same thing differently ("five-year CD" vs "60 Month
Certificate", "drive-thru" vs "Drive-up"). Paraphrases must reach the same facts,
so every variant is normalised here, once. Rules, in order:

  1. lower-case, plain hyphens, "a.m." -> "am", drop possessive "'s"
  2. number words -> digits, but ONLY before a time unit ("five-year" -> "5-year";
     a bare "one" is left alone: it appears 1,400+ times in prose)
  3. thousands separators removed ("$10,000" -> "10000")
  4. acronyms and synonyms rewritten to one canonical token (tables below)
  5. a term ("60-month", "5 years", "60 Months") becomes ONE token "<months>mo"
  6. plurals stemmed (rates -> rate, branches -> branch); no -ing/-ed stripping,
     because "checking" (the account) must not merge with "check"

Keyword lists elsewhere in the code are declared with `terms("...")`, which runs the
same tokenizer, so a list can never disagree with what tokenize() produces.
The tokenizer is pure (no data-dependent state): every snapshot's index and every
question are tokenized identically.
"""
from __future__ import annotations

import re

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty-five": 25, "thirty-two": 32,
    "twenty": 20, "twenty-four": 24, "thirty": 30, "thirty-six": 36, "forty": 40,
    "forty-eight": 48, "sixty": 60, "seventy-two": 72, "eighty-four": 84,
}
UNIT = r"(months?|mos?|years?|yrs?)"
NUMBER_WORD_RE = re.compile(
    rf"\b({'|'.join(sorted(NUMBER_WORDS, key=len, reverse=True))})[- ]?{UNIT}\b")

# Long forms -> acronym. Hand-picked rather than harvested at ingest so the tokenizer
# stays pure. Applied BEFORE the synonyms ("federal home loan bank" must not become
# "federal mortgage bank").
ACRONYMS = [
    (r"home equity lines? of credit", "heloc"),
    (r"health savings accounts?", "hsa"),
    (r"individual retirement accounts?", "ira"),
    (r"annual percentage yields?", "apy"),
    (r"annual percentage rates?", "apr"),
    (r"federal home loan bank", "fhlb"),
    (r"automated teller machines?", "atm"),
    (r"interactive teller machines?", "itm"),
    (r"certificates? of deposits?", "certificate"),
]
# Member wording -> site wording. Each line is one defensible equivalence.
SYNONYMS = [
    (r"(?:term )?share certificates?|term shares?|cds?", "certificate"),
    (r"drive[- ]?(?:up|thru|through)s?", "driveup"),
    (r"money markets?", "moneymarket"),
    (r"share drafts?", "checking"),
    (r"home loans?", "mortgage"),
    (r"cars?|vehicles?|automobiles?", "auto"),
    (r"telephone|phone number", "phone"),
    (r"updat(?:e|es|ed|ing)|modif(?:y|ies|ied)|edit(?:s|ed|ing)?", "change"),
    (r"refinanc(?:e|es|ed|ing)|refis?", "refinance"),
    (r"purchas(?:es|ed|ing)|buying an? (?:home|house)|home purchase", "purchase"),
    (r"hq|head ?quarter(?:s|ed)?", "headquarters"),
    # The site's own links use "Become a member" and "Join Dupaco" for the same action.
    (r"become (?:a |an )?(?:dupaco )?members?", "join"),
]
_PHRASES = [(re.compile(rf"\b(?:{p})\b"), r) for p, r in ACRONYMS + SYNONYMS]

TOKEN_RE = re.compile(rf"(\d+)\s*-?\s*{UNIT}\b|[a-z0-9]+(?:\.[0-9]+)?")

STOPWORDS = frozenset(
    "a an and are as at be by can could do does for from has have how i in is it its me my "
    "of on or our so that the their there this to was we what when where which who will "
    "with would you your please tell know like any some".split()
)


def normalize(text: str) -> str:
    s = (text or "").lower()
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2019", "'")
    s = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", s)          # same rule as the scorer
    s = re.sub(r"'s\b", "", s)                           # "dupaco's" -> "dupaco"
    s = NUMBER_WORD_RE.sub(lambda m: f"{NUMBER_WORDS[m.group(1)]} {m.group(2)}", s)
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)            # "10,000" -> "10000"
    for pat, rep in _PHRASES:
        s = pat.sub(rep, s)
    return s


def stem(tok: str) -> str:
    """Plural -> singular, conservatively. Short words and -ss/-us/-is words are left
    alone (business, status, this); digits are never touched."""
    if tok.endswith("ibility") or tok.endswith("ability"):
        return tok[:-5] + "le"  # flexibility -> flexible, eligibility -> eligible
    if len(tok) <= 3 or not tok.endswith("s") or tok[0].isdigit() or tok.endswith(("ss", "us", "is")):
        return tok
    if tok.endswith("ies"):
        return tok[:-3] + "y"
    if tok.endswith(("ches", "shes", "xes", "sses")):
        return tok[:-2]
    return tok[:-1]


def tokenize(text: str) -> list[str]:
    out = []
    for m in TOKEN_RE.finditer(normalize(text)):
        if m.group(1):
            unit = m.group(2)
            out.append(f"{int(m.group(1)) * (12 if unit.startswith('y') else 1)}mo")
            continue
        tok = m.group(0)
        if tok in STOPWORDS or (len(tok) == 1 and tok.isalpha()):
            continue
        out.append(stem(tok))
    return out


def terms(text: str) -> frozenset[str]:
    """Declare a keyword list in plain words; it is stored in tokenized form."""
    return frozenset(tokenize(text))


def term_months(text: str) -> list[int]:
    """Every loan/certificate term mentioned, in months ("5-year" -> 60)."""
    return [int(t[:-2]) for t in tokenize(text) if t.endswith("mo") and t[:-2].isdigit()]
