"""Branch questions: hours and addresses.

Where the facts live (see NOTES): there is no site-wide hours page. Hours exist only
per branch, in branch-opening press releases; full addresses come from the /contact
locator; the contact-form dropdown names every branch but gives no hours or address.

Rules:
  B1 intent     hours words (hours, lobby, drive-up, "what time", open/close + a day)
                or address words (address, located, "where is"). A bare "when does X
                close" asks for a closure DATE and is left to the general handler.
  B2 named      words before "branch/office/location" (or a capitalised place after
                "in/at") name a branch. Every name word must match the same entry.
                  - entry with hours  -> its lobby + drive-up hours (and address)
                  - entry without     -> address if we have one, and "hours aren't
                                         listed" (never borrow another branch's hours)
                  - no entry          -> decline, no numbers
  B3 general    no branch named -> ONE fixed answer for every wording: the most common
                schedule, then every other schedule with its branches. Same numbers for
                every paraphrase; never asks for a ZIP code first.
  B4            the locator's "Open/Closed" status line is crawl-time state, never hours.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from text import terms, tokenize

LOCATOR_URL = "https://www.dupaco.com/contact"

HOURS_WORDS = terms("hours hour lobby drive-up")
# "today" is left out on purpose: "is the X branch still open today?" asks about status, not hours.
DAY_WORDS = terms("monday tuesday wednesday thursday friday saturday sunday weekend weekends weekday weekdays")
OPEN_CLOSE = terms("open opens close closes closing opening")
ADDRESS_WORDS = terms("address located location directions")
PHONE_WORDS = terms("phone call telephone")

# Street-type abbreviations so "Mullan Avenue" matches "W Mullan Ave."
ABBREV = {"avenue": "ave", "street": "st", "road": "rd", "drive": "dr", "boulevard": "blvd",
          "highway": "hwy", "lane": "ln", "west": "w", "east": "e", "north": "n", "south": "s", "so": "s"}
GENERIC = {"dupaco", "the", "a", "your", "our", "credit", "union", "cu", "community", "my", "local",
           "nearest", "closest", "which", "what", "this", "that", "any", "all", "each", "every", "s",
           "full", "service", "main", "new", "newest", "same", "do", "does", "is", "are", "keep",
           "hours", "hour", "lobby", "drive", "up", "thru", "through", "of", "for", "at", "in"}
NAME_BEFORE_RE = re.compile(r"([a-z0-9.'\- ]{2,40}?)\s+(?:branch|office|location)\b")
PLACE_AFTER_RE = re.compile(r"\b(?:in|at)\s+((?:[A-Z][a-zA-Z.\-]+\s?){1,3})")


def _name_tokens(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower().replace("-", ""))
    return {ABBREV.get(t, t) for t in toks}


def is_hours_question(question: str) -> bool:
    q = set(tokenize(question))
    low = question.lower()
    if re.search(r"\bstill (?:open|operating|there)\b", low):
        return False  # whether a branch still exists is a dated fact, not a schedule
    return bool(q & HOURS_WORDS) or "what time" in low or bool(q & OPEN_CLOSE and q & DAY_WORDS)


def is_address_question(question: str) -> bool:
    return (bool(set(tokenize(question)) & ADDRESS_WORDS)
            or bool(re.search(r"\bwhere is\b|\bwhere (?:can|do) i find\b", question, re.I)))


def named_branch(question: str) -> Optional[set[str]]:
    """Name tokens of the branch the member asks about, or None when no branch is named."""
    m = NAME_BEFORE_RE.search(question.lower())
    words = set()
    if m:
        # keep only the words right before "branch" (after the last generic word)
        chunk = m.group(1).split()
        tail = []
        for w in reversed(chunk):
            if w in GENERIC:
                break
            tail.append(w)
        words = _name_tokens(" ".join(reversed(tail)))
    if not words:
        m = PLACE_AFTER_RE.search(question[1:])  # skip the sentence-initial capital
        if m:
            words = _name_tokens(m.group(1))
    words -= GENERIC
    return words or None


class BranchDirectory:
    """Per snapshot: one record per branch we can name, merged from all three sources."""

    def __init__(self, store):
        self.records: list[dict] = []
        hours_by_branch: dict[str, dict] = {}
        for h in store.hours:
            rec = hours_by_branch.setdefault(h.branch or h.city or h.url, {
                "name": h.branch or h.city, "city": h.city, "address": h.address, "phone": h.phone,
                "lobby": None, "drive-up": None, "url": h.url,
                "published": store.pages[h.url].published if h.url in store.pages else None})
            rec[h.kind] = rec[h.kind] or h.lines
            rec["phone"] = rec["phone"] or h.phone
        for rec in hours_by_branch.values():
            rec["tokens"] = _name_tokens(" ".join(x for x in (rec["name"], rec["city"], rec["address"]) if x))
            self.records.append(rec)
        for b in store.branches:
            self.records.append({"name": f"{b.street} ({b.city})", "city": b.city, "address": b.address,
                                 "phone": None, "lobby": None, "drive-up": None, "url": b.url,
                                 "published": None, "tokens": _name_tokens(f"{b.city} {b.street}")})
        self.hours_records = [r for r in self.records if r["lobby"] or r["drive-up"]]

    def match(self, name: set[str]) -> list[dict]:
        return [r for r in self.records if name <= r["tokens"]]


def _sched(lines) -> str:
    return "; ".join(lines) if lines else "not listed"


def _branch_hours_text(rec: dict) -> str:
    where = f" ({rec['address']})" if rec.get("address") else ""
    return (f"{rec['name']} branch{where}: Lobby {_sched(rec['lobby'])}. "
            f"Drive-up {_sched(rec['drive-up'])}.")


def general_hours(directory: BranchDirectory) -> tuple[str, list[str]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in directory.hours_records:
        groups[(tuple(r["lobby"] or ()), tuple(r["drive-up"] or ()))].append(r)
    if not groups:
        return "", []
    # Most common schedule first; ties broken by the schedule text so the order is stable.
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    def names(rs):
        return ", ".join(sorted(r["name"] for r in rs))
    (lobby, drive), rs = ordered[0]
    lines = ["Hours vary by branch. The most common schedule "
             f"({names(rs)}):",
             f"- Lobby: {_sched(lobby)}",
             f"- Drive-up: {_sched(drive)}"]
    if len(ordered) > 1:
        lines.append("Other branches:")
        for (lobby, drive), rs in ordered[1:]:
            lines.append(f"- {names(rs)}: Lobby {_sched(lobby)}. Drive-up {_sched(drive)}.")
    lines.append("These hours come from Dupaco's branch announcements. For a specific branch, "
                 f"ask by name or use the branch locator at {LOCATOR_URL}.")
    urls = [LOCATOR_URL] + [r["url"] for _, rs in ordered for r in rs]
    return "\n".join(lines), urls


def answer_branch(question: str, directory: BranchDirectory):
    """Returns (text, source_urls, confident) or None if this is not a branch question."""
    if re.search(r"\bhow many\b|^\s*(?:the )?number of\b", question, re.I):
        return None  # a count ("how many branch locations"), not a branch lookup
    hours_q, addr_q = is_hours_question(question), is_address_question(question)
    phone_q = bool(set(tokenize(question)) & PHONE_WORDS)
    name = named_branch(question)
    if name is None and (hours_q or addr_q or phone_q):
        # "where is dupaco in galena illinois": a known branch city, however it is typed.
        low = " " + re.sub(r"[^a-z ]", " ", question.lower()) + " "
        cities = sorted({r["city"] for r in directory.records if r.get("city")}, key=len, reverse=True)
        city = next((c for c in cities if f" {c.lower()} " in low), None)
        if city:
            name = _name_tokens(city)
    if phone_q and name and not (hours_q or addr_q):
        # A branch's own number, or "not listed" - never another branch's or the main line.
        matches = directory.match(name)
        with_phone = [r for r in matches if r.get("phone")]
        if with_phone:
            r = with_phone[0]
            return f"The {r['name']} branch phone number is {r['phone']}.", [r["url"]], True
        if matches:
            return (f"The website doesn't list a direct phone number for the {matches[0]['name']} branch. "
                    f"Please check the branch locator at {LOCATOR_URL}.", [LOCATOR_URL], False)
        return ("I couldn't find a Dupaco branch by that name on the website. "
                f"Please check the branch locator at {LOCATOR_URL}.", [LOCATOR_URL], False)
    if not (hours_q or addr_q):
        return None
    if name is None:
        if hours_q:
            text, urls = general_hours(directory)
            return (text, urls, True) if text else None
        return None  # "where is Dupaco located" etc. -> general handler
    matches = directory.match(name)
    if not matches:
        return ("I couldn't find a Dupaco branch by that name on the website. "
                f"Please check the branch locator at {LOCATOR_URL}.", [LOCATOR_URL], False)
    with_hours = [r for r in matches if r["lobby"] or r["drive-up"]]
    if hours_q and with_hours:
        text = "\n".join(_branch_hours_text(r) for r in with_hours)
        return text, [r["url"] for r in with_hours] + [LOCATOR_URL], True
    full = next((r for r in matches if r["address"] and re.search(r"\d{5}", r["address"])),
                next((r for r in matches if r["address"]), None))
    label = matches[0]["name"]
    if addr_q and full:
        # Name it the way the member did: by city ("Galena") or by street ("Sycamore St.").
        street = full["address"].split(",")[0].lstrip("0123456789 ")
        what = full["city"] if _name_tokens(full["city"] or "") >= name else street
        text = f"The {what} branch is at {full['address']}."
        if hours_q:
            text += f" Its hours aren't listed on the website; see the branch locator at {LOCATOR_URL}."
        return text, [full["url"], LOCATOR_URL], True
    if full and hours_q:
        return (f"Hours for the branch at {full['address']} aren't listed on the website. "
                f"Please check the branch locator at {LOCATOR_URL}.", [LOCATOR_URL], False)
    # Named on the site (e.g. the contact-form dropdown) but no hours or address published.
    return (f"The website names a {label} branch but doesn't publish its "
            f"{'hours' if hours_q else 'address'}. Please check the branch locator at {LOCATOR_URL}.",
            [LOCATOR_URL], False)
