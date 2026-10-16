"""conflicts(): places where the website contradicts itself, with the value we trust.

What counts as ONE fact (so tiers and redesigns never look like contradictions):
  same product name (>50% word overlap), same qualifier flags (jumbo, IRA, business,
  intro, refinance...), same term, same balance/loan tier (exact), and model-year
  ranges that OVERLAP ("2023-26" and "2024-27" both cover a 2025 car). Every fact is
  anchored on its newest official row; older or lower-authority rows attach to the
  anchor they overlap most, so overlaps never chain across buckets.

Entry types (one entry per fact, all values listed oldest first):
  rate changed / stale copy   the rate sheet moved, and/or another page (calculator,
                              landing page, embedded table) still shows an older value.
                              Winner: the newest official value, i.e. what answer() says.
  expired promotion           an expired offer page states a different value.
  count drift                 "N <thing>" repeated in boilerplate on 3+ pages with
                              different N (branch counts). Winner: newest dated statement.
  running ordinal             "the fourth time" vs "the fifth time" (Forbes). Winner: newest.
Not listed: differences between tiers, metrics or products; year-tagged statistics
(294 in 2023 vs 256 in 2024 are both true); IRA-type differences on one sheet.
"""
from __future__ import annotations

import re
from collections import defaultdict

from contract import Conflict, Source
from general import NUMBER_RE, SELF_RE, YEAR_RE, _date, _sentences, running_ordinal
from rates import NAME_GENERIC, SOFT_FLAGS, _content
from text import tokenize

METRIC_NAMES = {"apy": "APY", "apr": "APR", "rate": "rate", "dividend": "dividend rate"}
STOP_PHRASE_RE = re.compile(r"\s(?:in|across|throughout|that|who|and|to|with|at|for|of)\s|[,.;:()]")


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") + "%" if round(v, 2) != v else f"{v:.2f}%"


def _value_str(row) -> tuple[str, ...]:
    """The row's values as printable strings, e.g. ("7.13% rate", "7.27% APR")."""
    out = []
    for k in ("rate", "dividend", "apy", "apr"):
        if k in row.metrics:
            lo, hi = row.metrics[k]
            out.append((_fmt(lo) if lo == hi else f"{_fmt(lo)}-{_fmt(hi)}") + f" {METRIC_NAMES[k]}")
    return tuple(out)


class _Obs:
    __slots__ = ("row", "snap", "taken_on", "tier", "expired", "name", "flags", "value")

    def __init__(self, row, st, expired):
        page = st.pages[row.url]
        prod = _content(tokenize(" ".join(row.section) + " " + row.label))
        path = _content(tokenize(row.url.split("/", 3)[-1].replace("/", " ").replace("-", " ")))
        self.row, self.snap, self.taken_on = row, st.snapshot_id, st.taken_on
        self.tier, self.expired = page.tier, expired
        self.flags = frozenset(f for f, (marks, _) in SOFT_FLAGS.items() if (prod | path) & marks)
        self.name = _content(tokenize((row.section[-1] if row.section else "") + " " + row.label)) - NAME_GENERIC
        self.value = _value_str(row)


def _overlap(a, b) -> int:
    """Years two model-year ranges share (-1 = not comparable)."""
    if a.row.year_range is None and b.row.year_range is None:
        return 0
    if a.row.year_range is None or b.row.year_range is None:
        return -1
    lo = max(a.row.year_range[0], b.row.year_range[0])
    hi = min(a.row.year_range[1], b.row.year_range[1])
    return hi - lo + 1 if hi >= lo else -1


def _same(a, b) -> bool:
    if a.flags != b.flags or a.row.term_months != b.row.term_months or a.row.amount_range != b.row.amount_range:
        return False
    if set(a.row.metrics) != set(b.row.metrics):
        return False  # a rate+APR row is never compared with an APY row
    union = a.name | b.name
    return bool(union) and len(a.name & b.name) / len(union) > 0.5 and _overlap(a, b) >= 0


def _topic(row, flags=frozenset()) -> str:
    """Readable topic with the term written the way members ask ("60-month")."""
    t = f"{row.term_months}-month " if row.term_months else ""
    name = " - ".join(x for x in ((row.section[-1] if row.section else ""), row.label) if x)
    if "business" in flags and "business" not in name.lower():
        name = "Business " + name  # the flag came from the page (/business-rates), not the label
    return f"{t}{name} {'/'.join(METRIC_NAMES[k] for k in ('rate', 'apy', 'apr') if k in row.metrics)}".strip()


def rate_conflicts(stores) -> list[Conflict]:
    obs = []
    for st in stores:
        for r in st.rows:
            if not st.pages[r.url].carried_from:
                obs.append(_Obs(r, st, False))
        for r in st.expired_rows:
            obs.append(_Obs(r, st, True))

    # Anchors: official (tiers 1-2) live rows, newest snapshot first.
    anchors: list[_Obs] = []
    for o in sorted(obs, key=lambda o: (o.taken_on, -o.tier), reverse=True):
        if o.tier <= 2 and not o.expired and not any(_same(o, a) for a in anchors):
            anchors.append(o)
    groups = defaultdict(list)
    for o in obs:
        cands = [a for a in anchors if _same(o, a)]
        if cands:
            best = max(cands, key=lambda a: (_overlap(o, a), a.taken_on))
            groups[id(best)].append(o)

    out = []
    for a in anchors:
        members = groups[id(a)]
        values = sorted({o.value for o in members}, key=lambda v: min(o.taken_on for o in members if o.value == v))
        if len(values) < 2:
            continue
        stale = [o for o in members if o.value != a.value and o.taken_on == a.taken_on and not o.expired]
        expired = [o for o in members if o.value != a.value and o.expired]
        over_time = [o for o in members if o.value != a.value and o.tier <= 2 and o.taken_on < a.taken_on]
        reasons = []
        if over_time:
            reasons.append(f"the official rate changed over time; the newest official value is effective "
                           f"{a.row.effective or a.taken_on}")
        if stale:
            pages = sorted({o.row.url for o in stale})
            reasons.append("still shown on " + ", ".join(p.split("dupaco.com")[-1] or p for p in pages[:3])
                           + " with an older effective date" if any(o.row.effective for o in stale)
                           else "still shown on lower-authority pages")
        if expired:
            reasons.append("an expired promotion page shows a different value")
        if not reasons:
            continue  # e.g. differences only between two older snapshots' minor pages
        seen, sources = set(), []
        for o in sorted(members, key=lambda o: (o.taken_on, o.tier)):
            k = (o.row.url, o.value)
            if k not in seen:
                seen.add(k)
                sources.append(Source(url=o.row.url, snapshot_id=o.snap, quote=" / ".join(o.value)))
        out.append(Conflict(topic=_topic(a.row, a.flags), values=[" / ".join(v) for v in values],
                            sources=sources[:8], resolution=" / ".join(a.value),
                            reason="; ".join(reasons)))
    return out




def _template(sentence: str) -> str:
    return re.sub(r"\d[\d,]*", "#", sentence.lower())


def count_conflicts(stores) -> list[Conflict]:
    """Boilerplate counts and running ordinals that differ between dated statements."""
    statements = {}   # (kind, key, url) -> (date, value, snapshot)
    pages_by_template = defaultdict(set)
    for st in stores:
        for p in st.passages:
            for s in _sentences(p.text):
                if re.search(r"\d", s):
                    pages_by_template[_template(s)].add(p.url)
    template_pages = defaultdict(int, {t: len(u) for t, u in pages_by_template.items()})
    for st in stores:
        for p in st.passages:
            page = st.pages[p.url]
            if page.tier > 4 or page.expired or page.carried_from:
                continue
            date = _date(st, p.url)
            if not date:
                continue
            for s in _sentences(p.text):
                # Counts: only boilerplate (the same sentence, numbers aside, on 3+ pages)
                # and never year-tagged statistics, which are all true for their year.
                if template_pages[_template(s)] >= 3 and not YEAR_RE.search(s) and SELF_RE.search(s):
                  for m in NUMBER_RE.finditer(s):
                    phrase = STOP_PHRASE_RE.split(s[m.end():m.end() + 50])[0].strip()
                    words = phrase.split()[:3]
                    key = tuple(tokenize(" ".join(words)))
                    if not key or any(t[0].isdigit() for t in key) or not words[0][:1].isalpha():
                        continue
                    statements[("count", key, p.url)] = (date, f"{m.group(1)} {' '.join(words)}", st.snapshot_id)
                for phrase, subject, verb in running_ordinal(s):
                    unit = phrase.split(" ", 1)[1].lower()
                    statements[("ordinal", (subject.lower(), verb, unit), p.url)] = (date, phrase, st.snapshot_id)

    by_key = defaultdict(list)
    for (kind, key, url), (date, value, snap) in statements.items():
        by_key[(kind, key)].append((date, value, url, snap))
    out = []
    for (kind, key), items in sorted(by_key.items()):
        values = {v.split()[0].lower() for _, v, _, _ in items}
        if len(values) < 2:
            continue
        if kind == "count" and len({u for _, _, u, _ in items}) < 3:
            continue  # only boilerplate repeated across pages; a one-off number is not a claim about now
        items.sort()
        newest = items[-1]
        ordered = []
        for _, v, _, _ in items:
            if v.split()[0].lower() not in [x.split()[0].lower() for x in ordered]:
                ordered.append(v)
        srcs, seen = [], set()
        for d, v, u, snap in reversed(items):
            if v.split()[0].lower() not in seen:
                seen.add(v.split()[0].lower())
                srcs.append(Source(url=u, snapshot_id=snap, quote=v))
        topic = (f"number of {' '.join(key)}" if kind == "count"
                 else f"how many times {key[0].title()} {key[1]} Dupaco")
        out.append(Conflict(topic=topic, values=ordered, sources=srcs[:6], resolution=newest[1],
                            reason=f"statements differ by date; the newest ({newest[0]}) is taken as current"))
    return out


def build_conflicts(stores) -> list[Conflict]:
    return rate_conflicts(stores) + count_conflicts(stores)
