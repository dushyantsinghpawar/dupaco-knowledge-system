#!/usr/bin/env python3
"""Look inside the ingest stores without running the scorer.

    python tools/inspect_store.py --summary
    python tools/inspect_store.py --snap 05 --rows /rates
    python tools/inspect_store.py --snap 01 --rows /rates --grep "model year"
    python tools/inspect_store.py --hours
    python tools/inspect_store.py --search "drive-up hours"
    python tools/inspect_store.py --page /calculators/vehicle-affordability
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "harness"), str(ROOT / "submission")]

from run_eval import load_snapshots  # noqa: E402
from store import acronym_map, build_store, store_summary  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", default=str(ROOT / "snapshots"))
    ap.add_argument("--snap", default="05", help="snapshot id prefix, e.g. 01")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--rows", metavar="PATH", help="rate rows from pages whose URL path contains PATH")
    ap.add_argument("--grep", default="", help="filter rows by text")
    ap.add_argument("--hours", action="store_true")
    ap.add_argument("--acronyms", action="store_true")
    ap.add_argument("--search")
    ap.add_argument("--page", metavar="PATH")
    a = ap.parse_args()

    snaps = load_snapshots(Path(a.snapshots))
    t = time.time()
    if a.summary:
        for s in snaps:
            print(store_summary(build_store(s)))
        print(f"built {len(snaps)} stores in {time.time() - t:.1f}s")
        return
    snap = next(s for s in snaps if s.snapshot_id.startswith(a.snap))
    st = build_store(snap)
    print(f"{st.snapshot_id}: built in {time.time() - t:.1f}s")
    if a.rows:
        for r in st.rows:
            if a.rows in r.url and a.grep.lower() in (r.text + str(r.section)).lower():
                print(f"[{st.pages[r.url].tier}] {r.text}\n      section={r.section} term={r.term_months} "
                      f"years={r.year_range} amount={r.amount_range} eff={r.effective} details={r.details_url}")
    if a.hours:
        for h in st.hours:
            print(f"{h.branch!r:28} {h.kind:9} {' | '.join(h.lines)}\n      {h.address} {h.phone} {h.url}")
    if a.acronyms:
        for k, v in sorted(acronym_map(st).items()):
            print(f"{k:8} {v}")
    if a.search:
        for i, score in st.index.search(a.search, k=8):
            p = st.passages[i]
            print(f"{score:6.2f} [{st.pages[p.url].tier}] {p.url}\n       {p.heading} :: {p.text[:200]}")
    if a.page:
        for url, info in st.pages.items():
            if a.page in url:
                print(info)


if __name__ == "__main__":
    main()
