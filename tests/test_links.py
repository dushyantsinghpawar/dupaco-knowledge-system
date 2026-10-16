import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))

from links import is_link_question  # noqa: E402


class LinkIntentTest(unittest.TestCase):
    def test_link_questions(self):
        for q in ["What link should I use to see Dupaco's CD / certificate rates?",
                  "What's the URL for Dupaco's branch and ATM locator?",
                  "How do I join Dupaco online?", "What's the website for auto loan rates?"]:
            self.assertTrue(is_link_question(q), q)

    def test_not_link_questions(self):
        # "online banking" as a topic, or "address" of a branch, is not a request for a URL.
        for q in ["What is the address of the Galena Branch?", "What is Dupaco's routing number?",
                  "What are the 30-year mortgage rates?"]:
            self.assertFalse(is_link_question(q), q)


if __name__ == "__main__":
    unittest.main()
