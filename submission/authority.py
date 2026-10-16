"""Page authority: how much we trust a page for a factual (numeric) claim.

Lower number = more authoritative. Tiers come from URL patterns plus a couple of
content signals, so every assignment can be explained in one sentence.

  1  official rate sheets            /rates, /business-rates
  2  product pages + official PDFs   /personal/*, /business/* (not resources), /hubfs/*.pdf
  3  other official pages            home, /contact, /about*, /membership, ...
  4  newsroom                        dated press releases
  5  editorial                       /learn/*, /business/resources/*, legacy blog URLs
  6  calculators, landing, campaigns /calculators/*, *-lp, limited-time campaign pages
  None = not indexed at all (navigation listings, tracking duplicates, spreadsheets)
"""
import re
from urllib.parse import urlparse

RATE_SHEETS = {"/rates", "/business-rates"}
SPREADSHEET_EXT = (".xlsx", ".xls", ".csv")
EXPIRED_RE = re.compile(r"!\[Expired\]|this offer has ended|offer has expired", re.I)
# "For a limited time" on a top-level page is how this site's campaigns read
# (e.g. /switch-and-earn); product pages live under /personal or /business.
CAMPAIGN_RE = re.compile(r"\bfor a limited time\b|\blimited[- ]time offer\b", re.I)


def canonical_url(url: str) -> str:
    """Lower-case host, no fragment, no trailing slash. Query strings are kept here
    (some are real pages); `is_excluded` decides which query variants to drop."""
    p = urlparse(url.strip())
    path = p.path.rstrip("/") or "/"
    q = f"?{p.query}" if p.query else ""
    host = p.netloc.lower()
    if host == "dupaco.com":
        host = "www.dupaco.com"
    return f"https://{host}{path}{q}"


def path_of(url: str) -> str:
    return (urlparse(url).path.rstrip("/") or "/").lower()


def is_excluded(url: str) -> bool:
    p = urlparse(url)
    path = p.path.lower()
    if "/tag/" in path or "/author/" in path:
        return True  # listing pages repeat article teasers, no facts of their own
    if path.endswith(SPREADSHEET_EXT):
        return True  # downloadable templates, not statements by the credit union
    if p.query:
        # HubSpot modal/tracking variants (privacy-policy?campaignId=..., ?hsLang=en) and
        # filtered listings (?category=) duplicate a real page.
        return True
    return False


def tier(url: str, markdown: str) -> int:
    path = path_of(url)
    if path in RATE_SHEETS or path.startswith("/rates/"):
        return 1
    if path.startswith("/calculators/") or path.endswith("-lp"):
        return 6
    if path.startswith("/newsroom/"):
        return 4
    if path.startswith(("/learn/", "/business/resources/", "/dupacodaily/")) or re.match(r"^/20\d\d/", path):
        return 5
    if path.startswith("/hubfs/"):
        return 2
    if path.startswith(("/personal/", "/business/")):
        return 2
    if path.count("/") == 1 and CAMPAIGN_RE.search(markdown or ""):
        return 6
    return 3


MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
# "offer ended 2025-09-30", "promotion ends June 30, 2026", "special rate valid through 6/30/2026"
ENDED_ON_RE = re.compile(
    r"\b(?:offer|promotion|promo|special|deal|rate)s?\b[^.\n]{0,40}?"  # an OFFER ended, not e.g. "voting ended"
    r"\b(?:ended|ends|expired|expires|valid (?:through|until)|available (?:through|until))\s+(?:on\s+)?"
    rf"(\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|(?:{MONTHS})\s+\d{{1,2}},?\s+\d{{4}})", re.I)


def _iso(date_text: str) -> str:
    d = date_text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", d)
    if m:
        y = int(m.group(3))
        return f"{y + 2000 if y < 100 else y:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.fullmatch(rf"({MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})", d, re.I)
    month = MONTHS.split("|").index(m.group(1).lower()) + 1
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def is_expired(markdown: str, as_of: str = "") -> bool:
    """Marked expired (image / "this offer has ended"), or states an end date that is
    before the snapshot date ("offer ended 2025-09-30")."""
    if EXPIRED_RE.search(markdown or ""):
        return True
    return bool(as_of) and any(_iso(m.group(1)) < as_of for m in ENDED_ON_RE.finditer(markdown or ""))
