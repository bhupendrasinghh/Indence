import math
import logging
from typing import Any, NamedTuple
from ...ports.models import RerankerClient, EmbeddingClient
from .retriever import RetrievalCandidate
from ..corpus.chunker import count_tokens

logger = logging.getLogger("evidence_platform.context_assembler")

def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)


class AssembledContext(NamedTuple):
    context_text: str
    selected_candidates: list[RetrievalCandidate]
    total_tokens: int


class ContextAssembler:
    """Reranks retrieval candidates and selects diverse chunks under a token budget using MMR."""

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        reranker_client: RerankerClient,
        lambda_val: float = 0.5
    ) -> None:
        self.embedding_client = embedding_client
        self.reranker_client = reranker_client
        self.lambda_val = lambda_val

    async def assemble_context(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        max_tokens_budget: int = 2048
    ) -> AssembledContext:
        if not candidates:
            return AssembledContext("", [], 0)

        # Study-type weights for evidence prioritization
        STUDY_TYPE_WEIGHTS = {
            "guideline": 1.35,
            "meta_analysis": 1.3,
            "systematic_review": 1.25,
            "rct": 1.2,
            "cohort_study": 1.0,
            "observational": 0.9,
            "case_report": 0.8,
            "other": 1.0
        }

        # 1. Rerank all candidates using the Cross-Encoder Reranker
        passage_texts = [c.text for c in candidates]
        raw_rerank_scores = await self.reranker_client.rerank(query, passage_texts)
        
        # Apply study-type weight boosting
        rerank_scores = []
        for candidate, score in zip(candidates, raw_rerank_scores):
            st = (candidate.study_type or "other").lower().strip()
            weight = STUDY_TYPE_WEIGHTS.get(st, 1.0)
            rerank_scores.append(score * weight)
        
        # Zip candidates with their rerank scores
        candidate_scores = list(zip(candidates, rerank_scores))
        
        # 2. Get dense embeddings for candidates to perform MMR diversity check
        embeddings = await self.embedding_client.embed_documents(passage_texts)
        candidate_vectors = [dense for dense, _ in embeddings]

        # Normalize rerank scores to [0, 1] for balanced MMR calculation
        min_score = min(rerank_scores) if rerank_scores else 0.0
        max_score = max(rerank_scores) if rerank_scores else 1.0
        score_range = max_score - min_score if max_score != min_score else 1.0

        normalized_scores = [
            (score - min_score) / score_range for score in rerank_scores
        ]

        # 3. Perform Maximal Marginal Relevance (MMR) selection loop
        selected_indices: list[int] = []
        remaining_indices = list(range(len(candidates)))
        
        current_tokens = 0
        selected_candidates: list[RetrievalCandidate] = []

        while remaining_indices:
            best_mmr_score = -float("inf")
            best_idx = -1

            for idx in remaining_indices:
                relevance = normalized_scores[idx]
                
                # Calculate redundancy: max similarity with already selected items
                redundancy = 0.0
                if selected_indices:
                    redundancy = max(
                        cosine_similarity(candidate_vectors[idx], candidate_vectors[s_idx])
                        for s_idx in selected_indices
                    )
                
                # MMR formula: lambda * relevance - (1 - lambda) * redundancy
                mmr_score = self.lambda_val * relevance - (1.0 - self.lambda_val) * redundancy
                
                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_idx = idx

            if best_idx == -1:
                break

            # Check if this item fits in the token budget
            candidate = candidates[best_idx]
            cand_tokens = count_tokens(candidate.text)
            
            if current_tokens + cand_tokens <= max_tokens_budget:
                selected_indices.append(best_idx)
                selected_candidates.append(candidate)
                current_tokens += cand_tokens
                
            remaining_indices.remove(best_idx)

        # 4. Sort selected candidates hierarchically:
        # Group by study/document, then preserve natural section ordering to maintain coherence
        # Sort key: (pmcid, study_type, chunk_id)
        selected_candidates.sort(key=lambda x: (x.pmcid or "", x.study_type or "", x.chunk_id or ""))

        # 5. Format the assembled context text block
        context_blocks = []
        for c in selected_candidates:
            # Format header for grounding reference
            ref_header = f"[Source: {c.pmcid} | Study Type: {c.study_type}]"
            context_blocks.append(f"{ref_header}\n{c.text}\n")

        assembled_text = "\n".join(context_blocks)
        total_tokens = count_tokens(assembled_text)

        return AssembledContext(
            context_text=assembled_text,
            selected_candidates=selected_candidates,
            total_tokens=total_tokens
        )
