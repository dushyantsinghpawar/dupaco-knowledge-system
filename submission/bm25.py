"""Okapi BM25 over passages, standard library only.

One index per snapshot: `as_of` then just means "pick the index", and IDF is
never skewed by pages from another era.
"""
import math
from collections import Counter, defaultdict

from text import tokenize


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths = []
        for i, doc in enumerate(docs):
            tf = Counter(tokenize(doc))
            self.lengths.append(sum(tf.values()))
            for term, n in tf.items():
                self.postings[term].append((i, n))
        self.n = len(docs)
        self.avg_len = (sum(self.lengths) / self.n) if self.n else 0.0

    def idf(self, term: str) -> float:
        df = len(self.postings.get(term, ()))
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int = 10, allowed=None) -> list[tuple[int, float]]:
        """Top-k (doc index, score). `allowed` optionally restricts doc indices."""
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            idf = self.idf(term)
            for i, tf in self.postings.get(term, ()):
                if allowed is not None and i not in allowed:
                    continue
                norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg_len)
                scores[i] += idf * tf * (self.k1 + 1) / (tf + norm)
        # Tie-break on doc index so repeated calls always return the same order.
        return sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:k]
