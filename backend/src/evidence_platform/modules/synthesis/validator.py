import re
import logging
from typing import Any
from ..retrieval.retriever import RetrievalCandidate

logger = logging.getLogger("evidence_platform.validator")

class NumericDosageValidator:
    """Verifies that all numeric values and dosages in LLM claims are grounded in source text."""

    def __init__(self) -> None:
        # Regex to find numbers, decimals, and percentages
        # Matches integers and floats like 15.4, 20%, 8.2, 100
        self.number_pattern = re.compile(r"\b\d+(?:\.\d+)?%?\b")

    def validate_claim(self, claim_text: str, referenced_candidates: list[RetrievalCandidate]) -> tuple[bool, list[str]]:
        """Validate a single claim text against a list of candidates.
        
        Returns a tuple of (is_valid, list_of_failed_numbers).
        """
        # Find all numbers in the claim
        claim_numbers = self.number_pattern.findall(claim_text)
        if not claim_numbers:
            return True, []

        failed_numbers = []
        
        # Combine all source texts to look for the numbers
        source_texts = [c.text.lower() for c in referenced_candidates]

        for num in claim_numbers:
            num_clean = num.strip().lower()
            
            # Simple check: is the number string present as a substring in any of the source texts?
            # We check if num_clean is a word boundary match in the source to avoid false positive matches
            # (e.g. '8' matching in '80' or '18').
            pattern = re.compile(rf"\b{re.escape(num_clean)}\b")
            found = False
            
            for src in source_texts:
                if pattern.search(src):
                    found = True
                    break
                    
            if not found:
                # Let's check for float equivalence if the string doesn't match exactly.
                # E.g. "15" in claim matching "15.0" in source, or vice versa
                try:
                    num_val = float(num_clean.replace("%", ""))
                    # Look for float equivalence in source text numbers
                    for src in source_texts:
                        src_nums = self.number_pattern.findall(src)
                        for sn in src_nums:
                            try:
                                sn_val = float(sn.replace("%", ""))
                                if abs(num_val - sn_val) < 1e-9:
                                    found = True
                                    break
                            except ValueError:
                                continue
                        if found:
                            break
                except ValueError:
                    pass

            if not found:
                failed_numbers.append(num)

        is_valid = len(failed_numbers) == 0
        return is_valid, failed_numbers

    def validate_answer(
        self,
        direct_claims: list[dict[str, Any]],
        evidence_map: dict[str, RetrievalCandidate]
    ) -> list[dict[str, Any]]:
        """Validate and enrich direct claims with numeric validation flags."""
        validated_claims = []
        
        for claim in direct_claims:
            claim_text = claim.get("text", "")
            evidence_ids = claim.get("evidence_ids", [])
            
            # Retrieve candidates corresponding to evidence_ids
            candidates = []
            for ev_id in evidence_ids:
                if ev_id in evidence_map:
                    candidates.append(evidence_map[ev_id])

            is_valid, failed_nums = self.validate_claim(claim_text, candidates)
            
            # Copy and enrich the claim dictionary
            enriched_claim = dict(claim)
            enriched_claim["numeric_validation_passed"] = is_valid
            enriched_claim["failed_clinical_values"] = failed_nums
            
            if not is_valid:
                logger.warning(
                    f"Clinical values validation failed for claim: '{claim_text}'. "
                    f"Failed numbers: {failed_nums}"
                )
                
            validated_claims.append(enriched_claim)

        return validated_claims
