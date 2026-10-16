"""Identifier, phone, who-is and "how do I" questions.

  P1 identifiers  routing number and phone numbers are pulled by pattern from official
                  pages (tiers 1-3). The value stated on the most distinct pages wins and
                  every official page stating it is cited. Phone answers quote the sentence
                  the number appears in, so the purpose (and any third party, e.g. "call
                  TruStage at ...") travels with the number. No purpose match -> main line.
  P2 how-to       "how do I change/report/replace ..." -> best official passage, quoted;
                  the main line is added when the passage gives no number. Never asks for
                  a member number or any authentication.
  P3 who-is       "Name, Title" / "Title Name" pairs about Dupaco roles. Rejected: another
                  organisation's role ("... CEO of Home Bancorp"), past roles ("former",
                  "retired", "long-time ... retirement"). The newest dated statement names
                  the current holder; an undated official page counts as dated today
                  (it is on the site as of this snapshot).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from store import page_date
from text import terms, tokenize

PHONE_RE = re.compile(r"\(?\b(\d{3})\)?[ .-]?(\d{3})-(\d{4})\b")
ROUTING_RE = re.compile(r"routing(?: and transit| transit)?(?: number| #|#)?\W{0,30}?\b(\d{9})\b", re.I)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])")

PHONE_Q = terms("phone call calling fax")
SERVICE_NUMBER_RE = re.compile(r"\b(?:customer|member) services?\b.*(?:\bnumber\b|#)", re.I)
MAIL_Q_RE = re.compile(r"\bmailing address\b|\bp\.?\s?o\.? box\b|\bwhere (?:do|can|should) i (?:mail|send)\b", re.I)
PO_BOX_RE = re.compile(r"P\.?\s?O\.?\s+Box\s+\d+,?\s+[A-Z][a-z]+,?\s+[A-Z]{2}\s*,?\s*\d{5}(?:-\d{4})?")
ROUTING_Q = terms("routing aba")
NOT_PURPOSE = terms(
    "phone call calling number numbers fax dupaco credit union what which who whom is the a to for "
    "do does i can my get reach contact talk speak someone person main general customer service")
HOWTO_RE = re.compile(r"\bhow (?:do|can|should|would) i\b|\bhow to\b|\bwhat(?:'s| is) the (?:best )?way to\b", re.I)
TASK_WORDS = terms("change report replace close add order reset enroll activate cancel stop freeze unfreeze "
                   "dispute transfer deposit withdraw remove block unlock lock")

# --- who-is
NAME = r"([A-Z][a-z]+(?: [A-Z]\.)?(?: [A-Z][a-z]+){1,2})"
ROLE = (r"(President(?: and | ?/ ?| & )(?:CEO|Chief Executive Officer)|Chief Executive Officer|CEO|"
        r"Chief [A-Z][a-z]+ Officer|[Bb]oard [Cc]hair(?:man|woman)?|[Cc]hair(?:man)? of the [Bb]oard)")
NAME_ROLE_RE = re.compile(NAME + r"(?:,| •| -)? (?:Dupaco(?:'s)? )?(?:Community )?(?:Credit Union(?:'s)? )?" + ROLE)
ROLE_NAME_RE = re.compile(r"(?:Dupaco(?:'s)? )?" + ROLE + r",? " + NAME)
NOT_A_PERSON = re.compile(r"Dupaco|Credit|Union|Foundation|Board|Committee|Bank|Savings|President|Officer|Chair|"
                          r"Center|Medical|Clinic|Hospital|College|University|County|City|Inc|Company|Group")
# Signature blocks read "Name • Title Name • Title": a name followed straight away by another
# title belongs to THAT title, not to the one before it.
NEXT_TITLE_RE = re.compile(r"^\s*(?:•|\b(?:[A-Z]\w+ )?(?:Committee|Officer|Chair|Treasurer|Secretary|President|Board|Vice|SVP|VP)\b)")
PAST_RE = re.compile(r"\b(former|formerly|retired|retirement|retiring|long-time|longtime|late|then-|previous|interim)\b", re.I)
OTHER_ORG_RE = re.compile(r"^\W{0,3}(?:of|at|for) (?!Dupaco)(?:the )?[A-Z]")
WHO_RE = re.compile(r"\bwho\b|\bname\b", re.I)  # "who is the CFO", "the COO's name"
ROLE_Q = {
    "board chair": terms("chair chairman chairwoman chairperson"),
    "officer": terms("cmo coo cfo cio cto"),
    "ceo": terms("ceo president head leader lead leads run runs charge"),
}
OFFICER_ABBR = {"cmo": "marketing", "coo": "operating", "cfo": "financial", "cio": "information", "cto": "technology"}


def _norm_phone(m: re.Match) -> str:
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _role_key(role: str) -> str:
    r = role.lower()
    if "chair" in r:
        return "board chair"
    if "ceo" in r or "chief executive" in r:
        return "ceo"
    return r  # "chief marketing officer"


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.split(text) if s.strip()]


class FactIndex:
    def __init__(self, store):
        self.store = store
        official = [p for p in store.passages if store.pages[p.url].tier <= 3 and not store.pages[p.url].carried_from]
        self.routing: dict[str, set] = defaultdict(set)
        self.mailing: dict[str, set] = defaultdict(set)
        self.phone_sents: list[tuple[str, str, str, frozenset]] = []   # number, sentence, url, tokens
        phone_pages: dict[str, set] = defaultdict(set)
        for p in official:
            for m in ROUTING_RE.finditer(p.text):
                self.routing[m.group(1)].add(p.url)
            for m in PO_BOX_RE.finditer(p.text):
                self.mailing[re.sub(r"\s+", " ", m.group(0))].add(p.url)
            for s in _sentences(f"{p.text}"):
                for m in PHONE_RE.finditer(s):
                    n = _norm_phone(m)
                    phone_pages[n].add(p.url)
                    self.phone_sents.append((n, s, p.url, frozenset(tokenize(p.heading + " " + s))))
        self.phone_pages = phone_pages
        self.main_line = max(sorted(phone_pages), key=lambda n: len(phone_pages[n])) if phone_pages else None

        # who-is: (role, name) -> newest date and sources
        self.roles: dict[str, dict[str, dict]] = defaultdict(dict)
        for p in store.passages:
            page = store.pages[p.url]
            if page.tier > 4:
                continue
            date = page_date(store, p.url) or None
            if not date:
                continue
            for s in _sentences(p.text):
                for rx, name_first in ((NAME_ROLE_RE, True), (ROLE_NAME_RE, False)):
                    for m in rx.finditer(s):
                        name, role = (m.group(1), m.group(2)) if name_first else (m.group(2), m.group(1))
                        if NOT_A_PERSON.search(name):
                            continue
                        if not name_first and NEXT_TITLE_RE.match(s[m.end():]):
                            continue
                        if PAST_RE.search(s[max(0, m.start() - 80):m.end()]):
                            continue
                        if OTHER_ORG_RE.match(s[m.end():] if name_first else s[m.end():]) or \
                                (not name_first and OTHER_ORG_RE.match(s[m.end(1):])):
                            continue
                        key = _role_key(role)
                        last = name.split()[-1]
                        rec = self.roles[key].setdefault(last, {"names": Counter(), "date": "", "urls": set(),
                                                                "title": role})
                        rec["names"][name] += 1
                        if date >= rec["date"]:
                            rec["date"], rec["title"] = date, role
                        rec["urls"].add(p.url)

    # ---------------------------------------------------------------- answers

    def routing_answer(self):
        if not self.routing:
            return None
        value = max(sorted(self.routing), key=lambda v: len(self.routing[v]))
        urls = sorted(self.routing[value], key=lambda u: (self.store.pages[u].tier, u))
        return f"Dupaco's routing number is {value}.", urls, True

    def mailing_answer(self):
        if not self.mailing:
            return None
        value = max(sorted(self.mailing), key=lambda v: len(self.mailing[v]))
        return f"Dupaco's mailing address is {value}.", sorted(self.mailing[value])[:3], True

    def phone_answer(self, question: str):
        purpose = set(tokenize(question)) - NOT_PURPOSE
        best, best_score = None, 0.0
        if purpose:
            for n, s, url, toks in self.phone_sents:
                score = len(purpose & toks)
                key = (score, len(self.phone_pages[n]), n)
                if score > 0 and (best is None or key > best[0]):
                    best = (key, n, s, url)
                    best_score = score
        if best and best_score > 0:
            _, n, s, url = best
            return s if len(s) < 400 else s[:400].rsplit(" ", 1)[0] + "...", [url], True
        if self.main_line:
            urls = sorted(self.phone_pages[self.main_line], key=lambda u: (self.store.pages[u].tier, u))[:3]
            return f"You can call Dupaco at {self.main_line}.", urls, True
        return None

    def who_answer(self, question: str):
        q = set(tokenize(question))
        low = question.lower()
        m = re.search(r"chief (\w+) officer", low)
        if q & ROLE_Q["board chair"]:
            key = "board chair"
        elif m and m.group(1) == "executive":
            key = "ceo"
        elif m:
            key = f"chief {m.group(1)} officer"
        elif q & ROLE_Q["officer"]:
            key = f"chief {OFFICER_ABBR[next(iter(q & ROLE_Q['officer']))]} officer"
        elif q & ROLE_Q["ceo"]:
            key = "ceo"
        else:
            return None
        holders = self.roles.get(key)
        if not holders:
            return (f"The website doesn't name a current {key.replace('ceo', 'CEO')}. "
                    "Please contact the credit union.", [], False)
        last, rec = max(sorted(holders.items()), key=lambda kv: (kv[1]["date"], sum(kv[1]["names"].values())))
        name = rec["names"].most_common(1)[0][0]
        urls = sorted(rec["urls"], key=lambda u: (self.store.pages[u].tier, -len(u)))[:3]
        return f"{name} is Dupaco's {rec['title']}.", urls, True

    def howto_answer(self, question: str):
        for i, _ in self.store.index.search(question, k=300):
            p = self.store.passages[i]
            if self.store.pages[p.url].tier <= 3:
                text = p.text if len(p.text) <= 500 else p.text[:500].rsplit(" ", 1)[0] + "..."
                if not PHONE_RE.search(text) and self.main_line:
                    text += f" You can also call Dupaco at {self.main_line}."
                return text, [p.url], True
        return None


def answer_fact(question: str, idx: FactIndex, phase: str):
    """phase "before_rates": routing, phone, who-is. phase "after_rates": how-to."""
    q = set(tokenize(question))
    if phase == "before_rates":
        if q & ROUTING_Q:
            return idx.routing_answer()
        if MAIL_Q_RE.search(question):
            res = idx.mailing_answer()
            if res:
                return res
        if WHO_RE.search(question):
            res = idx.who_answer(question)
            if res:
                return res
        if (q & PHONE_Q or SERVICE_NUMBER_RE.search(question)) and not HOWTO_RE.search(question):
            return idx.phone_answer(question)
        return None
    if HOWTO_RE.search(question) and q & TASK_WORDS:
        return idx.howto_answer(question)
    return None
