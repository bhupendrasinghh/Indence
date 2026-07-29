import logging
import asyncio
from typing import Any
from ...ports.models import EntailmentClient
from ..retrieval.retriever import RetrievalCandidate

logger = logging.getLogger("evidence_platform.abstention")

class AbstentionLayer:
    """Enforces clinical grounding policies by running parallel NLI checks and triggering abstention on safety failures."""

    def __init__(self, entailment_client: EntailmentClient, min_entailment_ratio: float = 0.5) -> None:
        self.entailment_client = entailment_client
        self.min_entailment_ratio = min_entailment_ratio

    async def verify_and_calibrate(
        self,
        status: str,
        direct_claims: list[dict[str, Any]],
        evidence_map: dict[str, RetrievalCandidate],
        abstention_reason: str | None = None
    ) -> tuple[str, list[dict[str, Any]], str | None]:
        """Perform parallel NLI checks on all claims. Trigger abstention if grounding ratio is too low."""
        # 1. If already in abstention state, pass through
        if status == "insufficient_evidence":
            return status, direct_claims, abstention_reason or "Insufficient evidence to answer query."

        if not direct_claims:
            return "insufficient_evidence", [], "No grounded clinical claims could be formulated."

        # 2. Prepare parallel tasks for each claim
        async def verify_single_claim(claim: dict[str, Any]) -> dict[str, Any]:
            claim_text = claim.get("text", "")
            evidence_ids = claim.get("evidence_ids", [])
            
            premises = []
            for ev_id in evidence_ids:
                if ev_id in evidence_map:
                    premises.append(evidence_map[ev_id].text)
            
            premise_text = " ".join(premises)
            
            if not premise_text:
                entailment_result = "neutral"
            else:
                entailment_result = await self.entailment_client.check_entailment(
                    premise=premise_text,
                    hypothesis=claim_text
                )
                
            enriched_claim = dict(claim)
            enriched_claim["entailment_status"] = entailment_result
            return enriched_claim

        # Execute all NLI checks concurrently using asyncio.gather
        logger.info(f"Running parallel NLI verification for {len(direct_claims)} claims...")
        verified_claims = await asyncio.gather(*[verify_single_claim(c) for c in direct_claims])

        # 3. Calculate grounding ratio
        entailed_count = sum(1 for c in verified_claims if c.get("entailment_status") == "entailment")
        grounding_ratio = entailed_count / len(direct_claims)
        logger.info(f"NLI Verification grounding ratio: {grounding_ratio:.2f} ({entailed_count}/{len(direct_claims)})")

        # 4. Enforce grounding threshold policy
        if grounding_ratio < self.min_entailment_ratio:
            logger.warning(
                f"Grounding ratio {grounding_ratio:.2f} is below minimum threshold {self.min_entailment_ratio:.2f}. "
                "Triggering dynamic abstention."
            )
            return (
                "insufficient_evidence",
                [],
                f"Answer verification failed. Grounded evidence ratio was too low ({grounding_ratio:.1%})."
            )

        # 5. Check if any claim directly contradicts the source text
        for claim in verified_claims:
            if claim["entailment_status"] == "contradiction":
                logger.warning(
                    f"Direct contradiction detected in claim: '{claim['text']}'. Triggering dynamic abstention."
                )
                return (
                    "insufficient_evidence",
                    [],
                    "Answer verification failed due to direct contradiction detected in generated claims."
                )

        return status, list(verified_claims), abstention_reason
