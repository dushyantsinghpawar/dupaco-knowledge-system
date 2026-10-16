"""Turn one page's markdown into typed records: rate rows, passages, hours blocks,
links and acronym definitions.

Everything here is per page and pure (no global state), so ingest is a simple loop
and each function can be tested on a pasted snippet.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------- cleaning

# Footnote markers glued to values: "4.20%1", "5.39% APR1". Left in, the scorer reads
# the trailing digit as an extra number and paraphrases stop agreeing.
FOOTNOTE_RE = re.compile(r"(%|\bAPR|\bAPY)\d{1,2}\b")
LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def clean(s: str) -> str:
    s = html.unescape(html.unescape(s or ""))  # "&amp;#45;" is double-escaped in one PDF
    s = s.replace("\\r\\n", " ").replace("\\*", "*").replace("**", "").replace("\\$", "$").replace("\\", "")
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ").replace("\u2019", "'")
    s = FOOTNOTE_RE.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_links(s: str) -> str:
    return LINK_RE.sub(r"\1", IMAGE_RE.sub("", s))


# ---------------------------------------------------------------- dates

MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
NUM_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
LONG_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b")
EFFECTIVE_RE = re.compile(r"effective\s+(?:as\s+of\s+)?(\d{1,2}/\d{1,2}/\d{2,4})", re.I)


def iso_from_numeric(s: str) -> Optional[str]:
    m = NUM_DATE_RE.search(s)
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y + 2000 if y < 100 else y
    return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else None


def iso_from_long(m: re.Match) -> str:
    return f"{int(m.group(3)):04d}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"


def published_date(markdown: str) -> Optional[str]:
    """Newsroom/blog bylines put the date on its own line near the top."""
    for line in markdown.splitlines()[:15]:
        m = LONG_DATE_RE.fullmatch(line.strip())
        if m:
            return iso_from_long(m)
    return None


# ---------------------------------------------------------------- numbers & qualifiers

PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
BARE_NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:APR|APY)?\s*$", re.I)
MONEY = r"\$\s?(\d[\d,]*(?:\.\d+)?)"
TERM_RE = re.compile(r"\b(\d+)\s*[- ]?\s*(months?|mo|years?|yr)\b", re.I)
YEAR_RANGE_RE = re.compile(r"\b(20\d\d|19\d\d)\s*-\s*(\d{2,4})\b")
SINGLE_YEAR_RE = re.compile(r"\b(20\d\d|19\d\d)\b")


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def parse_amount_range(s: str) -> Optional[tuple[float, float]]:
    """"$10,000 - $24,999.99", "$10,000-24,999", "$25,000+", "$100,000 or less", "$0.01+"."""
    s = clean(s)
    m = re.search(MONEY + r"\s*-\s*\$?\s?(\d[\d,]*(?:\.\d+)?)", s)
    if m:
        return (_money(m.group(1)), _money(m.group(2)))
    m = re.search(MONEY, s)
    if not m:
        return None
    v = _money(m.group(1))
    tail = s[m.end():].lower()
    if re.match(r"\s*(\+|and (up|over|above)|or more)", tail):
        return (v, float("inf"))
    if re.match(r"\s*(or less|and under|or below)", tail) or re.search(r"\bup to\s*$", s[:m.start()].lower()):
        return (0.0, v)
    return None  # a lone amount ("$1,000 minimum") is not a tier


def parse_term_months(s: str) -> Optional[int]:
    m = TERM_RE.search(s)
    if not m:
        return None
    n = int(m.group(1))
    return n * 12 if m.group(2).lower().startswith(("y",)) else n


def parse_year_range(s: str) -> Optional[tuple[int, int]]:
    """Model-year buckets: "2024-27" -> (2024, 2027); "2026-23" -> (2023, 2026);
    "2016 Model Year (and older)" -> (0, 2016). Parsed to numbers because the labels
    shift between snapshots (2023-26 became 2024-27)."""
    m = YEAR_RANGE_RE.search(s)
    if m:
        a, b = int(m.group(1)), m.group(2)
        b = int(b) if len(b) == 4 else (a // 100) * 100 + int(b)
        return (min(a, b), max(a, b))
    m = SINGLE_YEAR_RE.search(s)
    if m:
        y = int(m.group(1))
        low = s.lower()
        if "older" in low or "earlier" in low or "prior" in low:
            return (0, y)
        if "newer" in low:
            return (y, 9999)
        return (y, y)
    return None


PCT_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*%")


def parse_pct(s: str) -> Optional[tuple[float, float]]:
    """"12.50% - 19.50%" -> (12.5, 19.5); "5.39% APR" -> (5.39, 5.39); "7.89" -> (7.89, 7.89).
    Only an explicit "X% - Y%" is a range. A prose cell such as "1.99% intro APR ...
    after that 6.25%" keeps its FIRST value: two numbers in a sentence are two facts."""
    m = PCT_RANGE_RE.search(s)
    first = PCT_RE.search(s) or BARE_NUM_RE.match(s)
    # A range counts only if it IS the cell's leading value: in "2.90% APR for 6 mos. (after
    # that your rate will be 12.50% - 19.50% APR)" the fact is 2.90%, the range is a footnote.
    if m and (not first or m.start() <= first.start()):
        a, b = float(m.group(1)), float(m.group(2))
        return (min(a, b), max(a, b))
    vals = [float(v) for v in PCT_RE.findall(s)]
    if not vals:
        m = BARE_NUM_RE.match(s)
        vals = [float(m.group(1))] if m else []
    return (vals[0], vals[0]) if vals else None


# ---------------------------------------------------------------- tables

METRICS = ("dividend", "apy", "apr", "rate")
# Promo tables carry "... for 1st 6 Months" and "... After Promotional Period" side by
# side. They are different facts, so the intro columns get their own "<kind>_intro" key.
INTRO_HEADER_RE = re.compile(r"\b(1st|first|intro\w*|promo\w* rate)\b")


def is_metric(kind: str) -> bool:
    return kind.removesuffix("_intro") in METRICS


def column_kind(header: str) -> str:
    base = _base_column_kind(header)
    h = header.lower()
    if base in METRICS and INTRO_HEADER_RE.search(h) and "after" not in h:
        return base + "_intro"
    return base


def _base_column_kind(header: str) -> str:
    h = header.lower()
    if "dividend" in h:
        return "dividend"
    if "yield" in h or re.search(r"\bapy\b", h):
        return "apy"
    if "percentage rate" in h or re.search(r"\bapr\b", h):
        return "apr"
    if re.fullmatch(r"\s*(interest )?rate\s*", h):
        return "rate"
    if "point" in h:
        return "points"
    if "min" in h and "open" in h:
        return "min_open"
    if "min" in h and "earn" in h:
        return "min_earn"
    if "payment" in h:
        return "payment"
    if h.strip() == "term":
        return "term"
    if "balance" in h or "loan amount" in h:
        return "tier"
    return "label"


def cell_kind(header_kind: str, cell: str) -> str:
    """The cell's content beats its header: a calculator page labels its auto-loan
    columns "Account Type | Balance Tier | APY" while the cells say "60 Months" and
    "5.34% APR"."""
    if header_kind in METRICS and PCT_RE.search(cell):  # intro kinds keep their header
        if re.search(r"\bAPR\b", cell):
            return "apr"
        if re.search(r"\bAPY\b", cell):
            return "apy"
    pure_term = re.fullmatch(r"\d+\s*(months?|mo|years?)", cell.strip(), re.I)
    if header_kind in ("tier", "label") and pure_term:
        return "term"
    if header_kind == "term" and not pure_term:
        return "label"  # 01 puts "Purchase: 30-Year Fixed ... $100,000 or less" under "Term"
    return header_kind


@dataclass
class RateRow:
    url: str
    section: tuple[str, ...]        # nearest headings above the table, outermost first
    label: str                      # the row's own name ("17 Month Fixed Certificate Special")
    metrics: dict                   # kind -> (low, high), e.g. {"apy": (4.1, 4.1)}
    cells: dict                     # kind -> cleaned cell text, for quoting verbatim
    term_months: Optional[int] = None
    year_range: Optional[tuple[int, int]] = None
    amount_range: Optional[tuple[float, float]] = None
    effective: Optional[str] = None  # ISO date from the "Rates are effective as of" line
    details_url: Optional[str] = None
    notes: list = field(default_factory=list)  # footnote texts this row points to
    table: int = 0                  # index of the table on its page (rows of one table age together)

    @property
    def text(self) -> str:
        """A clean, footnote-free rendering we can quote in an answer."""
        sect = self.section[-1] if self.section else ""
        # "Share Savings - Share Savings" reads badly; keep the section only if it adds words.
        name = self.label if sect and sect.lower() in self.label.lower() else " - ".join(x for x in (sect, self.label) if x)
        quals = [self.cells[k] for k in ("tier", "term") if self.cells.get(k)]
        head = name + (f" ({', '.join(quals)})" if quals else "")
        names = {"dividend": "dividend rate", "apy": "APY", "apr": "APR", "rate": "rate"}
        vals = []
        order = ("rate", "dividend", "apy", "apr")  # rate before APR, dividend before APY
        for k in [f"{b}_intro" for b in order] + list(order):
            if k in self.cells:
                b = k.removesuffix("_intro")
                c = re.sub(r"\s+\d$", "", self.cells[k])  # spaced footnote marker: "... will apply. 4"
                v = c if re.search(rf"\b{names[b]}\b", c, re.I) else f"{c} {names[b]}"
                vals.append(v + (" (introductory)" if k.endswith("_intro") else ""))
        return f"{head}: {', '.join(vals)}"


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip()) and any(cells)


FOOTNOTE_LINE_RE = re.compile(r"^(\d)(?=[A-Z])(.{10,})")
DETAILS_LINK_RE = re.compile(r"\[(?:See details|Explore|Learn more)\]\(([^)\s]+)")


def heading_text(line: str) -> Optional[str]:
    """Headings are plain short lines on this site (both layouts), sometimes '#' or bold."""
    s = line.strip()
    m = re.match(r"^#{1,6}\s+(.+)", s)
    if m:
        return clean(m.group(1).strip("* "))
    m = re.fullmatch(r"\*\*([^*]{2,60})\*\*", s)
    if m:
        return clean(m.group(1))
    if not s or len(s) > 60 or s[0] in "|[!->*(_" or FOOTNOTE_LINE_RE.match(s):  # "_Minimum credit limit_" is a note
        return None
    if re.search(r"[.:!?,;]$", s) or "effective" in s.lower() or not re.search(r"[A-Za-z]{3}", s):
        return None
    if DAY_LINE_RE.match(s):
        return None  # "Sunday: Closed" belongs to an hours block, it is not a heading
    if re.search(r":\s*\S", s) and re.search(r"[\d$%]", s):
        return None  # "Lost debit card replacement fee: $2.50" is a fact, not a heading
    return clean(s)


def _table_blocks(lines: list[str]):
    """Yield (start, end, header_cells, body_rows) for each markdown table."""
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and _is_separator(_split_row(lines[i + 1])):
            header = _split_row(lines[i])
            j, body = i + 2, []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                body.append(_split_row(lines[j]))
                j += 1
            yield i, j, header, body
            i = j
        else:
            i += 1


def parse_tables(url: str, markdown: str) -> tuple[list[RateRow], list[str]]:
    """Rate rows from every table that has a rate/APY/APR column, plus plain
    "label: value" lines for other tables (fee schedules) so they can be searched."""
    lines = markdown.splitlines()
    blocks = list(_table_blocks(lines))
    rows, plain = [], []
    headings: list[str] = []
    last_section: tuple[str, ...] = ()
    cursor = 0
    for bi, (start, end, header, body) in enumerate(blocks):
        for line in lines[cursor:start]:
            h = heading_text(line)
            if h:
                headings.append(h)
        # Headings seen since the previous table; none means "same section continues".
        dedup = [h for i, h in enumerate(headings) if i == 0 or h != headings[i - 1]]
        if len(dedup) >= 2:
            section = tuple(dedup[-2:])
        elif dedup and last_section and last_section[0] != dedup[0]:
            # One new heading = a sibling product in the same group ("Credit Cards" >
            # "Platinum Visa", then just "Rewards Visa"): keep the group heading.
            section = (last_section[0], dedup[0])
        elif dedup:
            section = (dedup[0],)
        else:
            section = last_section
        headings, last_section, cursor = [], section, end

        # Metadata follows the table: effective date, footnotes, "See details" link.
        nxt = blocks[bi + 1][0] if bi + 1 < len(blocks) else len(lines)
        after = lines[end:nxt]
        effective = details = None
        footnotes = {}
        for line in after:
            if effective is None and (m := EFFECTIVE_RE.search(line)):
                effective = iso_from_numeric(m.group(1))
            if details is None and (m := DETAILS_LINK_RE.search(line)):
                details = m.group(1).split("?")[0]
            if (m := FOOTNOTE_LINE_RE.match(line.strip())) and m.group(1) not in footnotes:
                footnotes[m.group(1)] = clean(m.group(2))[:300]
        if effective is None:
            # Back-to-back tables (Free Checking + 1st Rate Checking) share one date line
            # printed after the last of them.
            for line in lines[end:end + 80]:
                if m := EFFECTIVE_RE.search(line):
                    effective = iso_from_numeric(m.group(1))
                    break

        kinds = [column_kind(clean(re.sub(r"\s*\d\s*$", "", h))) for h in header]
        table_rows = []
        for raw in body:
            cells, metrics, label_parts, markers = {}, {}, [], set()
            for hk, rc in zip(kinds, raw):
                markers |= set(re.findall(r"(?:%|APR|APY)(\d)\b", rc))
                c = clean(rc).strip("* ").strip()
                if not c or c.upper() in ("N/A", "NONE", "-"):
                    continue
                k = cell_kind(hk, c)
                if is_metric(k):
                    v = parse_pct(c)
                    if v:
                        metrics[k], cells[k] = v, c
                elif k == "label":
                    label_parts.append(c)
                else:
                    cells[k] = c
            if not metrics:
                continue
            label = " ".join(label_parts)
            context = " ".join(section) + " " + label
            row = RateRow(
                url=url, section=section, label=label, metrics=metrics, cells=cells,
                term_months=parse_term_months(cells.get("term", "") or label),
                year_range=parse_year_range(label) if "model year" in context.lower() else None,
                amount_range=parse_amount_range(cells.get("tier", "") or label),
                effective=effective, details_url=details, table=bi,
                notes=[footnotes[m] for m in sorted(markers) if m in footnotes],
            )
            table_rows.append(row)
        if table_rows:
            rows.extend(table_rows)
        else:
            for raw in body:
                cs = [clean(strip_links(c)) for c in raw if clean(c)]
                if len(cs) >= 2:
                    plain.append(f"{cs[0]}: {' | '.join(cs[1:])}")
                elif cs:
                    plain.append(cs[0])
    return rows, plain


# ---------------------------------------------------------------- passages

@dataclass
class Passage:
    url: str
    heading: str
    text: str
    is_row: bool = False  # one row of a non-rate table (a fee): a single fact, never merged


PRICE_LINE_RE = re.compile(r"\$\s?\d|\d\s?%|\bFree\b")


def parse_passages(url: str, title: str, markdown: str, extra_lines: list[str],
                   max_words: int = 120) -> list[Passage]:
    """Paragraphs merged up to ~max_words, each remembering its nearest heading.
    Tables are skipped (rate rows live in their own store); `extra_lines` carries the
    rendered rows of non-rate tables such as the fee schedule."""
    out, buf, heading = [], [], title
    def flush():
        if buf:
            out.append(Passage(url, heading, " ".join(buf)))
            buf.clear()
    for block in re.split(r"\n\s*\n", markdown):
        b = block.strip()
        if not b or b.startswith("|"):
            continue
        h = heading_text(b) if "\n" not in b else None
        if h:
            flush()
            heading = h
            continue
        lines = [clean(strip_links(x)) for x in b.splitlines() if x.strip()]
        priced = [x for x in lines if PRICE_LINE_RE.search(x)]
        if len(lines) >= 3 and len(priced) >= 0.6 * len(lines):
            # A price list written as plain lines ("Domestic wire $25.00 each"): one fact per
            # line, exactly like the rows of a fee table.
            flush()
            out.extend(Passage(url, heading, x, is_row=True) for x in priced)  # labels are not facts
            continue
        text = clean(strip_links(b))
        if re.search(r"(?:\.\.\.|\u2026)$", text):
            continue  # a teaser that previews another page ("... take the leap into..."): not a fact
        if len(text.split()) < 3 and not re.match(r"^[A-Z][\w ]{1,20}:\s*\S*\d", text):
            continue  # nav crumbs, lone buttons, "2 min read"; but keep "Phone: 800-373-7600"
        if sum(len(x.split()) for x in buf) + len(text.split()) > max_words:
            flush()
        buf.append(text)
    flush()
    # One passage per table row: bundled rows made every fee answer quote its neighbours.
    for line in extra_lines:
        if ":" in line:  # a single-cell row ("Safe-Deposit Box") is a section label, not a fact
            out.append(Passage(url, title, line, is_row=True))
    return out


# ---------------------------------------------------------------- hours

DAY = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?"
DAY_LINE_RE = re.compile(rf"^{DAY}(?:\s*(?:-|through|to|&|and)\s*{DAY})?\s*:\s*\S")
HOURS_HEAD_RE = re.compile(r"^\*\*(Lobby|Drive-up|Drive-thru|Drive-through)\*\*\s*(.*)$", re.I)
BRANCH_RE = re.compile(r"\b[Tt]he ([A-Z][\w.' -]{2,40}?)(?: Dupaco)? branch\b")
DATELINE_RE = re.compile(r"^([A-Z][A-Z .']+), (?:Iowa|Ill\.?|Illinois|Wis\.?|Wisconsin)\s*-")
PHONE_RE = re.compile(r"\(\d{3}\)\s?\d{3}-\d{4}|\b\d{3}-\d{3}-\d{4}\b")


def _address(text: str) -> Optional[str]:
    """"located at 4615 Crosse Pointe Blvd. at the intersection of ..." -> "4615 Crosse Pointe Blvd."
    Stops at the first clause break; a period after a one-letter abbreviation ("E.") is not one."""
    m = re.search(r"located at (\d+ .{3,80})", text)
    if not m:
        return None
    rest = m.group(1)
    cut = re.search(r" at the | at the corner| in [A-Z]|, and |, with |, (?:Iowa|Ill|Wis)|(?<!\b[A-Z])\.(?: |$)|,? and the phone", rest)
    return (rest[:cut.start()] + ("." if cut and rest[cut.start()] == "." else "")).strip() if cut else rest[:60].strip()


@dataclass
class HoursBlock:
    url: str
    branch: Optional[str]     # "East Washington", or the dateline city ("Grimes") when unnamed
    kind: str                 # "lobby" | "drive-up"
    lines: list[str]          # ["Monday-Thursday: 8:30 a.m.-5 p.m.", ...]
    address: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None  # dateline city ("MADISON, Wis.-") even when a branch name exists


def parse_hours(url: str, markdown: str) -> list[HoursBlock]:
    raw = markdown.splitlines()
    lines = [clean(l) for l in raw]
    city = next((m.group(1).title() for l in lines[:30] if (m := DATELINE_RE.match(l))), None)
    page_address = _address(" ".join(lines))
    out = []
    for i, line in enumerate(raw):
        # Matched on the raw line: the bold "**Lobby**" marker is what makes it a heading.
        m = HOURS_HEAD_RE.match(line.strip())
        if not m:
            continue
        kind = "lobby" if m.group(1).lower() == "lobby" else "drive-up"
        rest = clean(m.group(2))
        sched = [rest] if rest and DAY_LINE_RE.match(rest) else []
        for nxt in lines[i + 1:i + 16]:
            if not nxt:
                continue
            if DAY_LINE_RE.match(nxt):
                sched.append(nxt)
            else:
                break
        if not sched:
            continue
        # The branch is named in the sentence(s) just before "Branch hours are:".
        before = " ".join(lines[max(0, i - 12):i])
        b = BRANCH_RE.findall(before)
        p = PHONE_RE.findall(before)
        out.append(HoursBlock(url, b[-1].strip() if b else city, kind, sched,
                              _address(before) or page_address, p[-1] if p else None, city))
    return out


# ---------------------------------------------------------------- branch directory

@dataclass
class BranchEntry:
    city: str
    street: str
    address: Optional[str]   # full postal address when the page gives one
    url: str


LOCATOR_RE = re.compile(r"^Dupaco Branch\s*\n+\s*(.+?, [A-Z]{2}, \d{5})\s*$", re.M)
# The contact form's branch dropdown is flattened into one line:
# "Select a BranchAsbury: Saratoga Rd.Carroll: W Hwy 30Cedar Falls: ..." -> (city, street) pairs.
DROPDOWN_RE = re.compile(r"Select a Branch(.+)")
DROPDOWN_ITEM_RE = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?): ")


def parse_branch_directory(url: str, markdown: str) -> list[BranchEntry]:
    """Branch names and addresses from the /contact locator (full addresses, but only
    the first page of results) and from the contact-form dropdown (every branch, names
    only). Neither says a branch is OPEN: the dropdown still lists the closed Hy-Vee
    branch and the locator's "Open/Closed" line is just the status at crawl time."""
    out = []
    for m in LOCATOR_RE.finditer(markdown):
        addr = clean(m.group(1))
        parts = [x.strip() for x in addr.split(",")]
        out.append(BranchEntry(parts[-3] if len(parts) >= 4 else "", parts[0], addr, url))
    for m in DROPDOWN_RE.finditer(markdown):
        line = m.group(1)
        items = list(DROPDOWN_ITEM_RE.finditer(line))
        for a, b in zip(items, items[1:] + [None]):
            street = line[a.end():b.start() if b else len(line)].strip()
            if street:
                out.append(BranchEntry(a.group(1), clean(street), None, url))
    return out


# ---------------------------------------------------------------- links & acronyms

@dataclass
class Link:
    source_url: str
    anchor: str
    target: str


def parse_links(url: str, markdown: str) -> list[Link]:
    out = []
    for m in LINK_RE.finditer(IMAGE_RE.sub("", markdown)):
        anchor, target = clean(m.group(1)), m.group(2)
        if target.startswith(("http", "/")) and anchor:
            out.append(Link(url, anchor, target))
    return out


ACRONYM_RE = re.compile(r"((?:[A-Z][\w'’-]*\s+(?:(?:of|and|for|the|&)\s+)?){1,6})\(([A-Z]{2,6})\)")


def parse_acronyms(markdown: str) -> list[tuple[str, str]]:
    """"Home Equity Line of Credit (HELOC)" -> ("HELOC", "home equity line of credit"),
    kept only when the initials actually spell the acronym, to avoid junk pairs."""
    out = []
    for m in ACRONYM_RE.finditer(clean(markdown)):
        words = m.group(1).split()
        acr = m.group(2)
        content = [w for w in words if w.lower() not in ("of", "and", "for", "the", "&")]
        for start in range(len(content)):
            initials = "".join(w[0] for w in content[start:]).upper()
            if initials == acr:
                long_words = words[words.index(content[start]):]
                out.append((acr, " ".join(long_words).lower()))
                break
    return out


# ---------------------------------------------------------------- definitions

INTRO_LINE_RE = re.compile(r"([A-Za-z][A-Za-z' -]{2,60}):\s*$")
LIST_ITEM_RE = re.compile(r"^\s*[-*]\s+(?:\*\*)?([A-Z][A-Za-z .]{1,30}?)(?:\*\*)?:(?:\*\*)?\s*(.+)$")


def parse_definitions(url: str, markdown: str) -> list[tuple[str, str, str]]:
    """A phrase introducing a labelled list defines that phrase:
         "... the credit union's geographic charter area:"
         "- Iowa: Any county" / "- Illinois: Carroll, Jo Daviess, ..."
    -> ("charter area", "Iowa: Any county; Illinois: Carroll, ...", url).
    The key is the last two words before the colon, so other pages that use the
    phrase ("within our charter area") can find it."""
    lines = markdown.splitlines()
    out = []
    for i, line in enumerate(lines):
        m = INTRO_LINE_RE.search(clean(strip_links(line)).rstrip())
        if not m:
            continue
        items = []
        for nxt in lines[i + 1:i + 12]:
            if not nxt.strip():
                continue
            im = LIST_ITEM_RE.match(nxt)
            if not im:
                break
            items.append(f"{clean(im.group(1))}: {clean(strip_links(im.group(2)))}")
        if len(items) >= 2:
            words = re.findall(r"[a-z]+", m.group(1).lower())
            if len(words) >= 2:
                out.append((" ".join(words[-2:]), "; ".join(items), url))
    return out


# ---------------------------------------------------------------- rate lines outside tables

RATE_LINE_RE = re.compile(r"^\s*[-*]?\s*(?P<label>[^:|\n]{3,80}?)\s*:\s*(?P<value>\d+(?:\.\d+)?\s*%(?:\s*-\s*\d+(?:\.\d+)?\s*%)?)\s*(?P<unit>APY|APR)?\s*$")


def parse_rate_lines(url: str, markdown: str) -> list[RateRow]:
    """"6-month certificate APY: 3.50%" written as a plain line (no table) is the same
    fact as a table row. Only whole lines of the form "<label naming a rate>: X%" count,
    so a percent inside a sentence never becomes a rate row."""
    rows, heading = [], ""
    for line in markdown.splitlines():
        m = RATE_LINE_RE.match(clean(strip_links(line)))
        if not m:  # checked first: "6-month certificate APY: 3.50%" also looks like a short heading
            heading = heading_text(line) or heading
            continue
        label, value, unit = m.group("label"), m.group("value"), (m.group("unit") or "")
        words = f"{label} {unit}".lower()
        kind = ("apy" if re.search(r"\bapy\b|yield", words) else "apr" if re.search(r"\bapr\b|percentage rate", words)
                else "dividend" if "dividend" in words else "rate" if re.search(r"\brate\b", words) else None)
        pct = parse_pct(value)
        if not kind or not pct:
            continue
        rows.append(RateRow(url=url, section=(heading,) if heading else (), label=label.strip(),
                            metrics={kind: pct}, cells={kind: value + (f" {unit}" if unit else "")},
                            term_months=parse_term_months(label), amount_range=parse_amount_range(label),
                            table=-1))
    return rows
