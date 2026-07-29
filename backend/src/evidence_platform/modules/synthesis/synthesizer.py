import logging
from typing import Any, NamedTuple
from ...ports.models import LLMClient
from ..retrieval.context_assembler import AssembledContext
from ..retrieval.retriever import RetrievalCandidate

logger = logging.getLogger("evidence_platform.synthesizer")

class SynthesizedAnswer(NamedTuple):
    status: str  # "answer" | "insufficient_evidence"
    direct_claims: list[dict[str, Any]]
    summary_claims: list[dict[str, Any]]
    limitations: list[dict[str, Any]]
    abstention_reason: str | None
    evidence_map: dict[str, RetrievalCandidate]


class AnswerSynthesizer:
    """Invokes structured LLM generation client with evidence maps and context."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    async def synthesize(self, query: str, assembled: AssembledContext) -> SynthesizedAnswer:
        """Construct the evidence bundle, query the LLM, and map results to original chunks."""
        if not assembled.selected_candidates:
            return SynthesizedAnswer(
                status="insufficient_evidence",
                direct_claims=[],
                summary_claims=[],
                limitations=[],
                abstention_reason="No relevant clinical trials found in active snapshot.",
                evidence_map={}
            )

        # 1. Create a structured evidence bundle mapping reference IDs (E01, E02...) to chunks
        chunks_list = []
        evidence_map = {}
        
        for idx, cand in enumerate(assembled.selected_candidates):
            ref_id = f"E{idx + 1:02d}"
            evidence_map[ref_id] = cand
            
            chunks_list.append({
                "id": cand.chunk_id,
                "text": cand.text,
                "evidence_id": ref_id,
                "pmcid": cand.pmcid
            })

        evidence_bundle = {
            "snapshot_id": "active_snapshot",
            "chunks": chunks_list
        }

        # 2. Invoke the structured LLM client
        logger.info(f"Invoking LLM structured generation for query with {len(chunks_list)} chunks...")
        res = await self.llm_client.generate_answer(query, evidence_bundle)

        status = res.get("status", "answer")
        abstention_reason = res.get("abstention_reason")

        # Extract claims conforming to the structured output contract
        direct_claims = res.get("direct_answer_claims", [])
        summary_claims = res.get("evidence_summary_claims", [])
        limitations = res.get("limitations_claims", [])

        return SynthesizedAnswer(
            status=status,
            direct_claims=direct_claims,
            summary_claims=summary_claims,
            limitations=limitations,
            abstention_reason=abstention_reason,
            evidence_map=evidence_map
        )
