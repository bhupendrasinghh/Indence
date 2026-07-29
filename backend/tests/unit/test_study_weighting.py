import unittest
import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.adapters.models.mock import MockEmbeddingClient, MockRerankerClient
from evidence_platform.modules.retrieval.retriever import RetrievalCandidate
from evidence_platform.modules.retrieval.context_assembler import ContextAssembler


class TestStudyTypeWeighting(unittest.TestCase):

    def setUp(self):
        self.embedding_client = MockEmbeddingClient()
        self.reranker_client = MockRerankerClient()
        self.assembler = ContextAssembler(
            embedding_client=self.embedding_client,
            reranker_client=self.reranker_client,
            lambda_val=0.5
        )

        # Candidate 1: Observational study
        self.obs_candidate = RetrievalCandidate(
            chunk_id="chunk-obs", qdrant_point_id="pt-obs",
            text="Study text representing clinical outcomes of the drug.",
            score=0.8, document_id="doc-obs", pmcid="PMC888",
            study_type="observational", publication_date="2022", display_rights=True
        )

        # Candidate 2: RCT study with IDENTICAL text (so raw rerank score is exactly the same!)
        self.rct_candidate = RetrievalCandidate(
            chunk_id="chunk-rct", qdrant_point_id="pt-rct",
            text="Study text representing clinical outcomes of the drug.",
            score=0.8, document_id="doc-rct", pmcid="PMC999",
            study_type="rct", publication_date="2022", display_rights=True
        )

    def test_rct_priority_over_observational(self):
        candidates = [self.obs_candidate, self.rct_candidate]
        
        # Set max tokens budget to only allow one chunk to be selected
        res = asyncio.run(
            self.assembler.assemble_context(
                query="clinical outcomes of the drug",
                candidates=candidates,
                max_tokens_budget=15
            )
        )
        
        # Verify RCT candidate is selected due to weight boosting (1.2x vs 0.9x), even though texts are identical
        self.assertEqual(len(res.selected_candidates), 1)
        self.assertEqual(res.selected_candidates[0].chunk_id, "chunk-rct")


if __name__ == "__main__":
    unittest.main()
