import unittest
import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.adapters.models.mock import MockEntailmentClient
from evidence_platform.modules.retrieval.retriever import RetrievalCandidate
from evidence_platform.modules.synthesis.abstention import AbstentionLayer


class TestAbstentionLayer(unittest.TestCase):

    def setUp(self):
        self.entailment_client = MockEntailmentClient()
        self.abstention_layer = AbstentionLayer(self.entailment_client, min_entailment_ratio=0.5)

        self.candidate = RetrievalCandidate(
            chunk_id="chunk-1", qdrant_point_id="pt-1",
            text="Trastuzumab deruxtecan works well.",
            score=0.9, document_id="doc-1", pmcid="PMC1",
            study_type="rct", publication_date="2022", display_rights=True
        )
        self.evidence_map = {"E01": self.candidate}

    def test_verify_all_entailed(self):
        claims = [
            {"text": "Trastuzumab deruxtecan works well.", "evidence_ids": ["E01"]}
        ]
        
        status, verified, reason = asyncio.run(
            self.abstention_layer.verify_and_calibrate("answer", claims, self.evidence_map)
        )
        
        self.assertEqual(status, "answer")
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["entailment_status"], "entailment")
        self.assertIsNone(reason)

    def test_verify_low_entailment_ratio_abstains(self):
        claims = [
            {"text": "Trastuzumab deruxtecan works well.", "evidence_ids": ["E01"]},
            {"text": "Metformin does something unrelated.", "evidence_ids": ["E01"]},
            {"text": "Aspirin is unrelated.", "evidence_ids": ["E01"]}
        ]
        
        # 1/3 entailed = 33.3%, which is less than min_entailment_ratio (50%) -> should abstain!
        status, verified, reason = asyncio.run(
            self.abstention_layer.verify_and_calibrate("answer", claims, self.evidence_map)
        )
        
        self.assertEqual(status, "insufficient_evidence")
        self.assertEqual(len(verified), 0)
        self.assertIn("ratio was too low", reason)

    def test_verify_contradiction_abstains(self):
        claims = [
            {"text": "Trastuzumab deruxtecan works well.", "evidence_ids": ["E01"]},
            {"text": "This will contradict source.", "evidence_ids": ["E01"]}
        ]
        
        # MockEntailmentClient marks anything containing 'contradict' as contradiction -> should abstain!
        status, verified, reason = asyncio.run(
            self.abstention_layer.verify_and_calibrate("answer", claims, self.evidence_map)
        )
        
        self.assertEqual(status, "insufficient_evidence")
        self.assertEqual(len(verified), 0)
        self.assertIn("direct contradiction", reason)


if __name__ == "__main__":
    unittest.main()
