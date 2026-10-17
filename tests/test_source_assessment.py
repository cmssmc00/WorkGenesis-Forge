"""Regression for valid limitation lists being discarded as unsuitable sources."""
import copy
import unittest

from wgforge.workflow import UnsuitableSource, validate_source_assessment


class SourceAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.excerpt = "Of the 16 milestones due in FY24-Q3, eleven were completed."
        self.evidence = [{"record": {"id": "source"}, "extracted_text": self.excerpt}]
        self.assessment = [{"source_id": "source", "usable": True,
            "occupation_fit": "Supports project-management work on milestones, procurement delays and technical dependencies.",
            "professional_operations": ["Monitor and reconcile milestone status"],
            "evidence_excerpt": self.excerpt,
            "limitations": ["The report is a historical snapshot.", "No resource availability is supplied."]}]

    def test_list_preserves_every_item_without_changing_original(self):
        original = copy.deepcopy(self.assessment)
        result = validate_source_assessment(self.assessment, self.evidence)
        self.assertEqual(result[0]["limitations"], "The report is a historical snapshot.\nNo resource availability is supplied.")
        self.assertEqual(self.assessment, original)

    def test_existing_string_stays_unchanged(self):
        self.assessment[0]["limitations"] = "No resource availability is supplied."
        self.assertEqual(validate_source_assessment(self.assessment, self.evidence), self.assessment)

    def test_missing_or_malformed_limitations_still_rejected(self):
        for invalid in (None, [], [""], ["   "], ["One limitation", 7], {"note": "limitation"}):
            with self.subTest(invalid=invalid):
                self.assessment[0]["limitations"] = invalid
                with self.assertRaisesRegex(UnsuitableSource, "coverage limitations"):
                    validate_source_assessment(self.assessment, self.evidence)
        self.assessment[0].pop("limitations")
        with self.assertRaisesRegex(UnsuitableSource, "coverage limitations"):
            validate_source_assessment(self.assessment, self.evidence)

    def test_list_does_not_bypass_source_evidence_check(self):
        self.assessment[0]["evidence_excerpt"] = "Invented source text that is absent from the actual reference."
        with self.assertRaisesRegex(UnsuitableSource, "actual inspected source evidence"):
            validate_source_assessment(self.assessment, self.evidence)


if __name__ == "__main__":
    unittest.main()
