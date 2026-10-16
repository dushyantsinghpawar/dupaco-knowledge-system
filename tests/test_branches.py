import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))

from branches import is_address_question, is_hours_question, named_branch  # noqa: E402


class BranchIntentTest(unittest.TestCase):
    def test_hours_intent(self):
        for q in ["What are your lobby hours?", "What are the drive-thru hours?",
                  "What time does the Grimes branch open?", "Is the Platteville office open on Saturday?"]:
            self.assertTrue(is_hours_question(q), q)

    def test_status_and_dates_are_not_hours(self):
        # A closure date or "still open" is a dated fact, not a schedule.
        for q in ["When does the Hy-Vee branch close?", "Is the Hy-Vee branch still open today (October 2026)?"]:
            self.assertFalse(is_hours_question(q), q)

    def test_address_intent(self):
        self.assertTrue(is_address_question("What is the address of the Galena Branch?"))
        self.assertTrue(is_address_question("Where is the Sycamore Street branch?"))

    def test_named_branch(self):
        self.assertEqual(named_branch("What is the address of the Galena Branch?"), {"galena"})
        self.assertEqual(named_branch("lobby hours for the Mullan Avenue branch?"), {"mullan", "ave"})
        self.assertEqual(named_branch("What are hours in Platteville?"), {"platteville"})

    def test_general_questions_name_no_branch(self):
        for q in ["What are Dupaco's general lobby hours?", "Do all Dupaco branches keep the same lobby hours?",
                  "What are the drive-up hours?"]:
            self.assertIsNone(named_branch(q), q)


if __name__ == "__main__":
    unittest.main()
