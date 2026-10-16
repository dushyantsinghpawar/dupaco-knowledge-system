"""Everything we know about ONE snapshot, built once at ingest.

A snapshot is the whole site as of its date, so each store is built from that
snapshot alone: a page missing from snapshot N simply does not exist for answers
as of N. History across snapshots (for as_of and conflicts) is the list of stores.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

import authority
from bm25 import BM25
from parse import (BranchEntry, HoursBlock, Link, Passage, RateRow, clean, parse_acronyms, parse_branch_directory, parse_definitions, parse_hours, parse_rate_lines,
                   parse_links, parse_passages, parse_tables, published_date, strip_links)


@dataclass
class PageInfo:
    url: str                  # canonical
    title: str
    tier: int
    expired: bool
    published: Optional[str]  # byline date for newsroom/blog posts
    fetched_at: str
    # Set when the page was NOT in this snapshot and is carried forward from an earlier
    # one (value = taken_on of the last snapshot that had it). Carried pages are a
    # fallback only: never rates or identifiers, half weight in passages, and flagged.
    carried_from: Optional[str] = None


@dataclass
class SnapshotStore:
    snapshot_id: str
    taken_on: str
    pages: dict[str, PageInfo] = field(default_factory=dict)
    rows: list[RateRow] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)
    hours: list[HoursBlock] = field(default_factory=list)
    branches: list[BranchEntry] = field(default_factory=list)
    definitions: list[tuple[str, str, str]] = field(default_factory=list)  # (phrase, text, url)
    links: list[Link] = field(default_factory=list)
    acronyms: Counter = field(default_factory=Counter)
    index: Optional[BM25] = None
    # Expired promo pages never feed answers, but their values are kept for conflicts().
    expired_rows: list[RateRow] = field(default_factory=list)


def _content_key(markdown: str) -> str:
    return hashlib.sha1(clean(strip_links(markdown)).encode()).hexdigest()


def build_store(snapshot, carried: Optional[dict] = None) -> SnapshotStore:
    """`carried`: canonical url -> (page, taken_on of the last snapshot that had it), for
    pages an earlier snapshot had and this one does not."""
    st = SnapshotStore(snapshot.snapshot_id, snapshot.taken_on)
    carried = carried or {}

    # Pass 1: choose one copy per canonical URL and per identical content.
    chosen: dict[str, object] = {}
    for p in snapshot.pages:
        if authority.is_excluded(p.url):
            continue
        chosen.setdefault(authority.canonical_url(p.url), p)
    origin: dict[str, str] = {}
    for url, (p, seen) in carried.items():
        if url not in chosen and not authority.is_excluded(p.url):
            chosen[url], origin[url] = p, seen
    # "See details" targets are the URLs the site itself treats as canonical, so an
    # identical copy under another URL collapses onto them.
    preferred = set()
    for p in chosen.values():
        preferred.update(authority.canonical_url(l.target) for l in parse_links(p.url, p.markdown)
                         if "See details" in l.anchor or l.anchor == "Explore")
    by_content: dict[str, str] = {}
    for url in sorted(chosen, key=lambda u: (u not in preferred, len(u), u)):
        key = _content_key(chosen[url].markdown)
        if key in by_content:
            del chosen[url]
        else:
            by_content[key] = url

    # Pass 2: parse each kept page into the typed stores.
    for url in sorted(chosen):
        p = chosen[url]
        md = p.markdown
        info = PageInfo(url, p.title, authority.tier(url, md), authority.is_expired(md, snapshot.taken_on),
                        published_date(md), p.fetched_at, origin.get(url))
        st.pages[url] = info
        rows, plain = parse_tables(url, md)
        rows += parse_rate_lines(url, md)
        if info.expired:
            st.expired_rows.extend(rows)
            continue
        if not info.carried_from:  # a rate from a page no longer on the site never answers
            st.rows.extend(rows)
        st.passages.extend(parse_passages(url, p.title, md, plain))
        st.hours.extend(parse_hours(url, md))
        for b in parse_branch_directory(url, md):
            if all((b.city, b.street) != (x.city, x.street) for x in st.branches):
                st.branches.append(b)
        st.links.extend(parse_links(url, md))
        if info.tier <= 3:
            st.definitions.extend(parse_definitions(url, md))
        st.acronyms.update(parse_acronyms(md))

    st.index = BM25([f"{p.heading} {p.text}" for p in st.passages])
    return st


def acronym_map(store: SnapshotStore) -> dict[str, str]:
    """Most frequent expansion per acronym (several pages define APR differently)."""
    best: dict[str, tuple[int, str]] = {}
    for (acr, long), n in store.acronyms.items():
        if acr not in best or n > best[acr][0]:
            best[acr] = (n, long)
    return {a: v for a, (_, v) in best.items()}


def store_summary(st: SnapshotStore) -> dict:
    tiers = Counter(p.tier for p in st.pages.values())
    by_tier_rows = defaultdict(int)
    for r in st.rows:
        by_tier_rows[st.pages[r.url].tier] += 1
    return {
        "snapshot": st.snapshot_id, "pages": len(st.pages), "pages_by_tier": dict(sorted(tiers.items())),
        "expired_pages": sum(p.expired for p in st.pages.values()),
        "rate_rows": len(st.rows), "rate_rows_by_tier": dict(sorted(by_tier_rows.items())),
        "rows_missing_effective": sum(r.effective is None for r in st.rows),
        "passages": len(st.passages), "hours_blocks": len(st.hours), "links": len(st.links),
        "acronyms": len(acronym_map(st)),
    }
