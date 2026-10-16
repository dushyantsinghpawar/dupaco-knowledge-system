"""Dupaco knowledge system: entry point required by harness/contract.py.

answer() routes in a fixed order: links, branch hours/addresses, routing/phone/who-is,
rates, how-to passages, then general facts and counts from passages
fallback (general facts arrive later in Phase 5); conflicts() arrives in Phase 6.
"""
from __future__ import annotations

import calendar
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # the harness puts harness/ on sys.path before loading us
    from contract import Answer, Conflict, Source
except ImportError:  # running outside the harness (debug scripts, tests)
    _h = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness" / "contract.py").exists()), None)
    if _h is None:
        raise
    sys.path.insert(0, str(_h))
    from contract import Answer, Conflict, Source

from authority import canonical_url
from branches import BranchDirectory, answer_branch
from conflicts import build_conflicts
from facts import FactIndex, answer_fact
from general import GeneralIndex, answer_general
from links import LinkIndex, answer_link
from rates import RATE_ASK_RE, RateIndex, answer_rate, is_rate_question
from store import SnapshotStore, build_store


def _row_sources(rows, snapshot_id) -> list:
    """The product page the rate sheet itself links to ("See details") first, then the
    page the row was read from."""
    urls = []
    for r in rows:
        for u in (r.details_url, r.url):
            if u and canonical_url(u) not in urls:
                urls.append(canonical_url(u))
    return [Source(url=u, snapshot_id=snapshot_id) for u in urls[:4]]


MONTH_NAMES = "january|february|march|april|may|june|july|august|september|october|november|december"
# Only an explicit scoping phrase sets the date. Other dates in a question are content
# ("the branch closes March 31, 2026"), not "answer as of".
SCOPE_RE = re.compile(
    rf"\b(?:as of|as at|back in|in|during)\s+(?:early |mid-|late )?({MONTH_NAMES})\s+(?:(\d{{1,2}}),?\s+)?(\d{{4}})\b"
    r"|\bas of\s+(\d{4}-\d{2}-\d{2})\b", re.I)


def scope_date(question: str) -> Optional[str]:
    """"What was the jumbo rate in May 2026?" -> "2026-05-31" (end of the named month)."""
    m = SCOPE_RE.search(question)
    if not m:
        return None
    if m.group(4):
        return m.group(4)
    month = [x for x in MONTH_NAMES.split("|")].index(m.group(1).lower()) + 1
    year = int(m.group(3))
    day = int(m.group(2)) if m.group(2) else calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{day:02d}"


class DupacoKnowledge:
    def __init__(self):
        self.stores: list[SnapshotStore] = []  # oldest -> newest
        self._rate_idx: dict[str, RateIndex] = {}
        self._branches: dict[str, BranchDirectory] = {}
        self._links: dict[str, LinkIndex] = {}
        self._facts: dict[str, FactIndex] = {}
        self._general: dict[str, GeneralIndex] = {}
        # Every page seen so far that is still "live": canonical url -> (page, last taken_on).
        self._live_pages: dict[str, tuple] = {}
        self._conflicts: Optional[list] = None

    def ingest(self, snapshot) -> None:
        present = {canonical_url(p.url): (p, snapshot.taken_on) for p in snapshot.pages}
        # A page missing from this snapshot is carried forward (demoted), in case this
        # crawl was partial; see PageInfo.carried_from.
        carried = {u: v for u, v in self._live_pages.items() if u not in present}
        self._live_pages = {**carried, **present}
        st = build_store(snapshot, carried)
        self._conflicts = None
        self.stores.append(st)
        self.stores.sort(key=lambda s: s.taken_on)
        self._rate_idx[st.snapshot_id] = RateIndex(st)  # build now so answer() stays fast
        self._branches[st.snapshot_id] = BranchDirectory(st)
        self._links[st.snapshot_id] = LinkIndex(st)
        self._facts[st.snapshot_id] = FactIndex(st)
        self._general[st.snapshot_id] = GeneralIndex(st)

    def store_for(self, as_of: Optional[str]) -> SnapshotStore:
        """Latest snapshot taken on or before as_of; dates before the first crawl fall
        back to the first snapshot (the earliest view of the site we have)."""
        if not as_of:
            return self.stores[-1]
        eligible = [s for s in self.stores if s.taken_on <= as_of[:10]]
        return eligible[-1] if eligible else self.stores[0]

    def rate_index(self, st: SnapshotStore) -> RateIndex:
        if st.snapshot_id not in self._rate_idx:
            self._rate_idx[st.snapshot_id] = RateIndex(st)
        return self._rate_idx[st.snapshot_id]

    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        st = self.store_for(as_of or scope_date(question))
        sid = st.snapshot_id
        def rate_rows_for(q: str) -> list:
            # A link to rates comes from the same rows a rate answer would quote.
            if not is_rate_question(q):
                return []
            r = answer_rate(st, q, self.rate_index(st))
            return r[1] if r and r[2] else []

        res = answer_link(question, st, self._links[sid], rate_rows_for)
        if res is None:
            res = answer_branch(question, self._branches[sid])
        if res is None:
            res = answer_fact(question, self._facts[sid], "before_rates")
        if res is not None:
            return self._with_sources(res, sid)
        if is_rate_question(question):
            res = answer_rate(st, question, self.rate_index(st))
            if res is not None:
                text, rows, confident = res
                return Answer(text=text, sources=_row_sources(rows, st.snapshot_id), confident=confident)
            if RATE_ASK_RE.search(question):
                return Answer(text="That rate isn't listed on the published rates. Please contact the credit union for a quote.",
                              confident=False)
        res = answer_fact(question, self._facts[sid], "after_rates")
        if res is not None:
            return self._with_sources(res, sid)
        as_of_year = int((as_of or scope_date(question) or st.taken_on)[:4])
        return self._with_sources(answer_general(question, self._general[sid], as_of_year), sid)

    @staticmethod
    def _with_sources(res, sid: str) -> Answer:
        text, urls, confident = res
        # Cite every page the answer quotes (the general hours answer quotes all of them).
        srcs = list(dict.fromkeys(canonical_url(u) for u in urls))[:8]
        return Answer(text=text, sources=[Source(url=u, snapshot_id=sid) for u in srcs], confident=confident)

    def conflicts(self) -> list[Conflict]:
        if self._conflicts is None:  # computed once, after all snapshots are in
            self._conflicts = build_conflicts(self.stores)
        return self._conflicts


def build() -> DupacoKnowledge:
    return DupacoKnowledge()
