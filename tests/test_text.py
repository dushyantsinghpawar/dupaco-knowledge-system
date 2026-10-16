"""Tokenizer guarantees. Run: python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))

from text import STOPWORDS, term_months, terms, tokenize  # noqa: E402


class Paraphrases(unittest.TestCase):
    """Wordings a member might use must tokenize like the site's own wording."""

    def same(self, a, b):
        self.assertEqual(sorted(set(tokenize(a))), sorted(set(tokenize(b))), f"{a!r} vs {b!r}")

    def test_terms(self):
        self.same("5-year certificate", "60 Months certificate")
        self.same("five year CD", "60-month certificate")
        self.same("one-year cd", "12 Month certificate")
        self.same("30-year mortgage", "30-Year home loan")

    def test_synonyms(self):
        self.same("drive-thru hours", "Drive-up hours")
        self.same("drive through", "drive-up")
        self.same("car loan", "auto loan")
        self.same("home equity line of credit", "HELOC")
        self.same("Dupaco's routing number", "dupaco routing number")

    def test_amounts_and_times(self):
        self.assertIn("10000", tokenize("$10,000 - $24,999.99"))
        self.assertIn("am", tokenize("8 a.m."))
        self.assertIn("4.10", tokenize("4.10% APY"))

    def test_things_that_must_stay_apart(self):
        self.assertNotEqual(tokenize("checking"), tokenize("check"))
        self.assertNotEqual(tokenize("business"), tokenize("busines"))
        self.assertNotIn("1", tokenize("one of the best"))  # bare number words stay words
        self.assertNotIn("federal mortgage bank", " ".join(tokenize("Federal Home Loan Bank")))

    def test_term_months(self):
        self.assertEqual(term_months("What's the 60-month auto rate?"), [60])
        self.assertEqual(term_months("a five-year CD"), [60])
        self.assertEqual(term_months("30-year fixed"), [360])


class Consistency(unittest.TestCase):
    """Keyword lists are declared via terms(); this only works if tokenizing the
    tokenizer's own output changes nothing (otherwise a stored keyword and a fresh
    question token could disagree)."""

    SAMPLES = [
        "What's the APR for a 60-month auto loan on a 2025 model-year vehicle?",
        "Branches, policies, rates, hours, businesses, statuses, IRAs and HELOCs",
        "Purchase: 30-Year Fixed Conventional $100,000 or less 7.13% rate",
        "Drive-up Monday-Friday: 8 a.m.-6 p.m. Saturday: 9 a.m.-noon",
    ]

    def test_idempotent(self):
        for s in self.SAMPLES:
            once = tokenize(s)
            self.assertEqual(tokenize(" ".join(once)), once, s)

    def test_terms_matches_tokenize(self):
        for s in self.SAMPLES:
            self.assertEqual(terms(s), frozenset(tokenize(s)))

    def test_stopwords_really_vanish(self):
        # A stopword that normalisation rewrites before the stopword check would leak.
        for w in STOPWORDS:
            self.assertEqual(tokenize(w), [], w)


if __name__ == "__main__":
    unittest.main()
