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


class UnusedSourceTests(unittest.TestCase):
    """A second, unrelated anchor judged unusable no longer discards finished work."""

    def setUp(self):
        self.good = "Of the 16 milestones due in FY24-Q3, eleven were completed."
        self.other = "Proposal Name: Environmental Review and Permitting Project"
        self.evidence = [{"record": {"id": "good"}, "extracted_text": self.good},
                         {"record": {"id": "other"}, "extracted_text": self.other}]
        self.usable = {"source_id": "good", "usable": True,
                       "occupation_fit": "Supports project-management work on milestones, procurement delays and dependencies.",
                       "professional_operations": ["Monitor and reconcile milestone status"],
                       "evidence_excerpt": self.good, "limitations": ["Historical snapshot."]}
        self.unused = {"source_id": "other", "usable": False,
                       "occupation_fit": "Fits the occupation generally but belongs to a different organisation and period.",
                       "limitations": ["Not included in this package."]}

    def test_default_still_rejects_any_unusable_source(self):
        with self.assertRaisesRegex(UnsuitableSource, "judged unusable"):
            validate_source_assessment([self.usable, self.unused], self.evidence)

    def test_allow_unused_keeps_verdicts_and_normalizes_limitations(self):
        result = validate_source_assessment([self.usable, self.unused], self.evidence, allow_unused=True)
        self.assertEqual([row["usable"] for row in result], [True, False])
        self.assertEqual(result[1]["limitations"], "Not included in this package.")

    def test_allow_unused_still_needs_one_usable_source(self):
        other = dict(self.unused, source_id="good")
        with self.assertRaisesRegex(UnsuitableSource, "judged unusable"):
            validate_source_assessment([other, self.unused], self.evidence, allow_unused=True)

    def test_unused_source_needs_explicit_false_and_reason(self):
        for change in ({"usable": None}, {"usable": "no"}, {"occupation_fit": "different org"}, {"occupation_fit": None}):
            with self.subTest(change=change):
                with self.assertRaisesRegex(UnsuitableSource, "explicit usable=false"):
                    validate_source_assessment([self.usable, dict(self.unused, **change)], self.evidence, allow_unused=True)

    def test_every_source_must_still_be_judged(self):
        with self.assertRaisesRegex(UnsuitableSource, "judge every source"):
            validate_source_assessment([self.usable], self.evidence, allow_unused=True)

    def test_usable_sources_keep_full_evidence_checks(self):
        bad = dict(self.usable, evidence_excerpt="Invented text that is not in the real source at all.")
        with self.assertRaisesRegex(UnsuitableSource, "actual inspected source evidence"):
            validate_source_assessment([bad, self.unused], self.evidence, allow_unused=True)


if __name__ == "__main__":
    unittest.main()
