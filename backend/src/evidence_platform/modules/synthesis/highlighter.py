import re
import logging
from typing import Any
from sqlalchemy.orm import Session

from ...db.models.models import Chunk, EvidenceUnit
from ..retrieval.retriever import RetrievalCandidate

logger = logging.getLogger("evidence_platform.highlighter")

def split_sentences_with_offsets(text: str) -> list[tuple[str, int, int]]:
    """Split text into sentences while tracking their start and end character offsets."""
    # Matches sentence endings followed by spaces
    sentence_endings = re.compile(r"(?<=[.!?])\s+")
    
    sentences = []
    start = 0
    
    for match in sentence_endings.finditer(text):
        end = match.start()
        s_text = text[start:end].strip()
        if s_text:
            sentences.append((s_text, start, start + len(s_text)))
        start = match.end()
        
    # Add trailing sentence
    s_text = text[start:].strip()
    if s_text:
        sentences.append((s_text, start, start + len(s_text)))
        
    return sentences


class SpanHighlighter:
    """Aligns generated claim references (e.g. E01.S1) to absolute database character spans."""

    def __init__(self, db_session: Session) -> None:
        self.db = db_session

    def align_claims(
        self,
        direct_claims: list[dict[str, Any]],
        evidence_map: dict[str, RetrievalCandidate]
    ) -> list[dict[str, Any]]:
        """Map claim sentence_ids to exact character offsets relative to their parent EvidenceUnit/Section."""
        aligned_claims = []

        for claim in direct_claims:
            claim_text = claim.get("text", "")
            sentence_ids = claim.get("sentence_ids", [])
            
            aligned_spans = []
            
            for sid in sentence_ids:
                # Parse sentence ID e.g. "E01.S2" -> ref_id="E01", sentence_no=2
                parts = sid.split(".S")
                if len(parts) != 2:
                    continue
                
                ref_id, s_no_str = parts[0], parts[1]
                try:
                    s_idx = int(s_no_str) - 1 # 1-indexed to 0-indexed
                except ValueError:
                    s_idx = 0

                candidate = evidence_map.get(ref_id)
                if not candidate:
                    continue

                # Query database Chunk to find its start_char offset
                chunk = self.db.query(Chunk).filter_by(id=candidate.chunk_id).first()
                if not chunk:
                    continue
                
                # Split candidate text into sentences with offsets within the chunk text
                sentences_with_offsets = split_sentences_with_offsets(candidate.text)
                if not sentences_with_offsets:
                    continue

                # Clamp index to available sentences
                target_idx = min(s_idx, len(sentences_with_offsets) - 1)
                target_idx = max(0, target_idx)
                
                s_text, s_start, s_end = sentences_with_offsets[target_idx]
                
                # Fetch absolute offsets relative to the parent section
                eu = self.db.query(EvidenceUnit).filter_by(id=chunk.evidence_unit_id).first()
                
                # Compute absolute start/end chars relative to Section text
                absolute_start = chunk.start_char + s_start
                absolute_end = chunk.start_char + s_end
                
                # If there's a parent EvidenceUnit, offsets could be offset by it
                if eu:
                    absolute_start += eu.start_char
                    absolute_end += eu.start_char

                aligned_spans.append({
                    "sentence_id": sid,
                    "text": s_text,
                    "pmcid": candidate.pmcid,
                    "section_start_char": absolute_start,
                    "section_end_char": absolute_end
                })

            enriched_claim = dict(claim)
            enriched_claim["aligned_spans"] = aligned_spans
            aligned_claims.append(enriched_claim)

        return aligned_claims
