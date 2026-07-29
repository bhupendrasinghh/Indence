import unittest
import sys
import asyncio
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.adapters.models.mock import MockEmbeddingClient, MockRerankerClient
from evidence_platform.modules.retrieval.retriever import RetrievalCandidate
from evidence_platform.modules.retrieval.context_assembler import ContextAssembler


class SemanticMockEmbeddingClient(MockEmbeddingClient):
    """Subclass of MockEmbeddingClient that yields semantically meaningful vectors for MMR testing."""
    async def embed_documents(self, documents: list[str]) -> list[tuple[list[float], dict[str, Any]]]:
        results = []
        for doc in documents:
            if "Trastuzumab" in doc or "T-DXd" in doc:
                # Highly similar vector
                dense = [1.0] + [0.0] * 1023
            elif "Physician" in doc:
                # Orthogonal vector
                dense = [0.0, 1.0] + [0.0] * 1022
            else:
                dense = [0.0] * 1024
            results.append((dense, {}))
        return results


class TestContextAssembler(unittest.TestCase):

    def setUp(self):
        self.embedding_client = SemanticMockEmbeddingClient()
        self.reranker_client = MockRerankerClient()
        self.assembler = ContextAssembler(
            embedding_client=self.embedding_client,
            reranker_client=self.reranker_client,
            lambda_val=0.5
        )

        # Create some mock candidates
        self.candidate1 = RetrievalCandidate(
            chunk_id="chunk-1", qdrant_point_id="pt-1",
            text="Trastuzumab deruxtecan (T-DXd) yields excellent PFS in HER2-low metastatic breast cancer.",
            score=0.9, document_id="doc-1", pmcid="PMC001",
            study_type="rct", publication_date="2022", display_rights=True
        )
        
        # Candidate 2 is highly redundant but has slightly lower keyword overlap
        self.candidate2 = RetrievalCandidate(
            chunk_id="chunk-2", qdrant_point_id="pt-2",
            text="T-DXd yields excellent results in metastatic breast cancer.",
            score=0.88, document_id="doc-1", pmcid="PMC001",
            study_type="rct", publication_date="2022", display_rights=True
        )

        # Candidate 3 is unique about standard chemotherapy
        self.candidate3 = RetrievalCandidate(
            chunk_id="chunk-3", qdrant_point_id="pt-3",
            text="Physician choice chemotherapy is the standard comparator in breast cancer trials.",
            score=0.7, document_id="doc-2", pmcid="PMC002",
            study_type="guideline", publication_date="2021", display_rights=True
        )

    def test_mmr_diversification(self):
        candidates = [self.candidate1, self.candidate2, self.candidate3]
        
        res = asyncio.run(
            self.assembler.assemble_context(
                query="Does T-DXd improve PFS in HER2-low breast cancer?",
                candidates=candidates,
                max_tokens_budget=30
            )
        )
        
        # Verify both candidate1 and candidate3 are selected, but candidate2 (redundant) is excluded
        selected_ids = [c.chunk_id for c in res.selected_candidates]
        
        self.assertEqual(len(selected_ids), 2)
        self.assertIn("chunk-1", selected_ids)
        self.assertIn("chunk-3", selected_ids)
        self.assertNotIn("chunk-2", selected_ids)

    def test_token_budget_limit(self):
        candidates = [self.candidate1, self.candidate3]
        
        # Set a small token budget (only enough for one chunk)
        res = asyncio.run(
            self.assembler.assemble_context(
                query="T-DXd",
                candidates=candidates,
                max_tokens_budget=20  # tiny budget
            )
        )
        # Should only select one candidate (whichever has higher relevance)
        self.assertEqual(len(res.selected_candidates), 1)


if __name__ == "__main__":
    unittest.main()
