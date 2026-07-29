import unittest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.modules.retrieval.retriever import RetrievalCandidate
from evidence_platform.modules.synthesis.validator import NumericDosageValidator


class TestNumericDosageValidator(unittest.TestCase):

    def setUp(self):
        self.validator = NumericDosageValidator()
        self.candidate = RetrievalCandidate(
            chunk_id="chunk-1", qdrant_point_id="pt-1",
            text="Median PFS was 15.4 months in the T-DXd group versus 8.2 months in chemotherapy.",
            score=0.9, document_id="doc-1", pmcid="PMC123",
            study_type="rct", publication_date="2022", display_rights=True
        )
        self.evidence_map = {"E01": self.candidate}

    def test_validation_success(self):
        claim = {
            "text": "T-DXd achieved a median PFS of 15.4 months.",
            "evidence_ids": ["E01"]
        }
        res = self.validator.validate_answer([claim], self.evidence_map)
        
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]["numeric_validation_passed"])
        self.assertEqual(res[0]["failed_clinical_values"], [])

    def test_validation_failure_hallucination(self):
        claim = {
            "text": "T-DXd achieved a median PFS of 18.2 months.",
            "evidence_ids": ["E01"]
        }
        res = self.validator.validate_answer([claim], self.evidence_map)
        
        self.assertEqual(len(res), 1)
        self.assertFalse(res[0]["numeric_validation_passed"])
        self.assertEqual(res[0]["failed_clinical_values"], ["18.2"])

    def test_float_equivalence(self):
        # Claim says '15' instead of '15.4' (should fail) or '15.40' (should pass)
        claim_exact_float = {
            "text": "PFS was 15.40 months.",
            "evidence_ids": ["E01"]
        }
        res = self.validator.validate_answer([claim_exact_float], self.evidence_map)
        self.assertTrue(res[0]["numeric_validation_passed"])


if __name__ == "__main__":
    unittest.main()
