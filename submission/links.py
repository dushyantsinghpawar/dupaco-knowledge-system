"""Link questions: "what link / URL / website ...", "how do I join online".

Rules:
  L1 intent     link words, or an action done online ("join ... online", "apply online")
  L2 rate topic the link the rate sheet itself gives for those rows ("See details"),
                using the same product match as rate answers, plus the full rate sheet
  L3 otherwise  anchor-text consensus: each target URL is described by the anchor texts
                that point to it; score = question overlap (IDF-weighted) plus how many
                distinct pages use that link. Dupaco-owned hosts only; articles only
                when the member asks for an article.
  L4 online     an online action prefers the application host (join.dupaco.com), with
                the best www page as a second link
  L5            never build a URL: only links that appear in this snapshot are returned
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from urllib.parse import urlparse

from authority import canonical_url
from authority import tier as authority_tier
from text import terms, tokenize

LINK_WORDS = terms("link links url urls website websites webpage webpages site page")
LINK_RE = re.compile(r"\b(link|url|website|web ?page|web address|web site)s?\b", re.I)
ONLINE_ACTION_RE = re.compile(r"\b(join|apply|enroll|sign up|open|become a member)\b.*\bonline\b|\bonline\b.*\b(join|apply|enroll|sign up|open)\b", re.I)
ACTION_WORDS = terms("join apply enroll open become member signup online")
RATE_LINK_WORDS = terms("rate rates apr apy")
SHEET_WORDS = terms("full sheet all complete list current every")
ARTICLE_WORDS = terms("article articles blog blogs post posts guide guides story")
# Words that are about asking for a link, not about what the link is for.
NOT_TOPIC = LINK_WORDS | terms(
    "what which where how use should could would see find view get go visit dupaco credit union "
    "official current online the for to of a an")
GENERIC_ANCHORS = terms("keep reading read more continue learn more see details explore click here")
DUPACO_HOSTS = ("www.dupaco.com", "join.dupaco.com", "shine.dupaco.com")
APPLICATION_HOST = "join.dupaco.com"
ARTICLE_PATHS = ("/learn/", "/newsroom/", "/business/resources/", "/dupacodaily/")
RATE_SHEET = "https://www.dupaco.com/rates"


# "Where can I see/find ... online?" asks for a page, like "what's the link for ...".
LOOK_ONLINE_RE = re.compile(r"\bwhere (?:can|do|should) i (?:see|find|view|check|look up)\b.*\bonline\b", re.I)


def is_link_question(question: str) -> bool:
    return bool(LINK_RE.search(question) or ONLINE_ACTION_RE.search(question) or LOOK_ONLINE_RE.search(question))


class LinkIndex:
    """Per snapshot: target URL -> words of the anchors pointing at it, and the set of
    pages that link to it (site-wide consensus)."""

    def __init__(self, store):
        words = defaultdict(set)
        pages = defaultdict(set)
        tiers = {}
        self.exact = set()  # targets exactly as written, fragments included
        for l in store.links:
            t = l.target
            p = urlparse(t)
            if p.netloc.lower() not in DUPACO_HOSTS:
                continue
            self.exact.add(t.split("?")[0])
            key = canonical_url(t.split("#")[0].split("?")[0])
            anchor = set(tokenize(l.anchor)) - GENERIC_ANCHORS
            words[key] |= anchor | set(tokenize(urlparse(key).path.replace("/", " ").replace("-", " ")))
            pages[key].add(l.source_url)
            src = store.pages.get(canonical_url(l.source_url))
            tiers[key] = min(tiers.get(key, 9), src.tier if src else 9)
        # Pages nothing links to (e.g. the fee-schedule PDF) are still findable by their own
        # title and path; they just have no consensus.
        for url, info in store.pages.items():
            if info.carried_from or info.expired or urlparse(url).netloc.lower() not in DUPACO_HOSTS:
                continue
            words[url] |= set(tokenize(info.title + " " + urlparse(url).path.replace("/", " ").replace("-", " ")))
            pages.setdefault(url, set())
        # Tier of the TARGET page; a target that is only linked (not crawled) is tiered by its URL.
        self.target_tier = {u: (store.pages[u].tier if u in store.pages else authority_tier(u, "")) for u in words}
        self.rate_sheet = next((u for u, i in sorted(store.pages.items()) if i.tier == 1 and u.endswith("/rates")), None)
        self.words, self.pages, self.tiers = dict(words), dict(pages), tiers
        df = defaultdict(int)
        for ws in self.words.values():
            for w in ws:
                df[w] += 1
        n = len(self.words) or 1
        self.idf = {w: math.log(1 + n / d) for w, d in df.items()}

    def ranked(self, question: str, allow_articles: bool) -> list[tuple[float, str]]:
        topic = set(tokenize(question)) - NOT_TOPIC
        if not topic:
            return []
        out = []
        for url, ws in self.words.items():
            tier = self.target_tier.get(url, 3)
            if not allow_articles and tier >= 4:
                continue  # newsroom/blog/legacy posts are articles, not the page a member wants
            overlap = sum(self.idf.get(t, 0.0) for t in topic & ws)
            if overlap <= 0:
                continue
            # consensus (how many pages link there) + authority of the TARGET page
            out.append((overlap + 0.5 * math.log(1 + len(self.pages[url])) + 0.5 * max(0, 3 - tier), url))
        out.sort(key=lambda x: (-x[0], x[1]))  # URL breaks ties so answers never flip
        return out

    def deep_link(self, url: str, fragment: str) -> str:
        """Use the site's own deep link (e.g. "#rates") when the site writes one."""
        for form in (url + "#" + fragment, url + "/#" + fragment):
            if form in self.exact:
                return url + "#" + fragment
        return url


def answer_link(question: str, store, link_idx: LinkIndex, rate_rows_for):
    """Returns (text, urls, confident) or None if this is not a link question.
    `rate_rows_for(question)` gives the rate rows the rate engine would answer with."""
    if not is_link_question(question):
        return None
    q = set(tokenize(question))

    # Ask the rate engine about the topic only: "link"/"page" are not product words.
    rows = rate_rows_for(re.sub(r"\b(?:link|url|website|web ?page|page|site|online)s?\b", " ", question, flags=re.I))
    if rows:
        counts = defaultdict(int)
        for r in rows:
            if r.details_url:
                counts[canonical_url(r.details_url)] += 1
        if counts:
            page = max(sorted(counts), key=lambda u: counts[u])
            url = link_idx.deep_link(page, "rates")
            return (f"You can see those rates at {url} (the full rate sheet is at {RATE_SHEET}).",
                    [url, RATE_SHEET], True)

    # Rates with no product named ("link to the full rate sheet") -> the official rate sheet.
    if link_idx.rate_sheet and q & RATE_LINK_WORDS and not (set(tokenize(question)) - NOT_TOPIC - RATE_LINK_WORDS - SHEET_WORDS):
        return f"Dupaco's full rate sheet is at {link_idx.rate_sheet}.", [link_idx.rate_sheet], True
    ranked = link_idx.ranked(question, allow_articles=bool(q & ARTICLE_WORDS))
    if not ranked:
        return ("That link isn't listed on the website. Please contact the credit union for help.", [], False)
    best = ranked[0][1]
    if ONLINE_ACTION_RE.search(question) or q & ACTION_WORDS:
        apps = [u for _, u in ranked if urlparse(u).netloc == APPLICATION_HOST]
        info = [u for _, u in ranked if urlparse(u).netloc == "www.dupaco.com"]
        if apps:  # "apply for membership", "join online": the application site
            second = f" (more details: {info[0]})" if info else ""
            return (f"You can do that online at {apps[0]}{second}.", [apps[0]] + info[:1], True)
    return (f"Here is the link: {best}", [best], True)
