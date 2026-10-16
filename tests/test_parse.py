import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))

from parse import parse_pct, parse_year_range  # noqa: E402


class ParsePctTest(unittest.TestCase):
    def test_leading_value_wins_over_later_range(self):
        # 01-04 Platinum Visa intro cell: the fact is 2.90%, the range is the after-intro rate.
        cell = "2.90% APR for 6 mos. (after that your rate will be 12.50% - 19.50% APR)"
        self.assertEqual(parse_pct(cell), (2.9, 2.9))

    def test_real_range(self):
        self.assertEqual(parse_pct("12.50% - 19.50% APR"), (12.5, 19.5))

    def test_model_year_buckets(self):
        self.assertEqual(parse_year_range("2024-27 Model Year"), (2024, 2027))
        self.assertEqual(parse_year_range("Model Year 2026-23"), (2023, 2026))
        self.assertEqual(parse_year_range("2016 Model Year (and older)"), (0, 2016))


if __name__ == "__main__":
    unittest.main()
