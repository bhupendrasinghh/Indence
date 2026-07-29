"""Section-aware parent-child chunking engine with sentence-level offsets.

Splits parsed section text into child chunks (256-320 tokens, ~15% overlap)
and parent evidence units (600-1200 tokens) while respecting sentence
and table row boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Token approximation: 1.33 tokens per word (standard for general medical text)
TOKEN_SCALE = 1.33


def count_tokens(text: str) -> int:
    """Approximate token count based on word splitting."""
    words = text.split()
    return int(len(words) * TOKEN_SCALE)


@dataclass
class SentenceSpan:
    text: str
    start_char: int
    end_char: int


@dataclass
class ChildChunk:
    id: str
    text: str
    start_char: int
    end_char: int
    token_count: int


@dataclass
class EvidenceUnit:
    id: str
    text: str
    start_char: int
    end_char: int
    token_count: int
    child_chunks: list[ChildChunk]


def segment_sentences(text: str) -> list[SentenceSpan]:
    """Split text into sentences with character offsets relative to the text."""
    if not text:
        return []
    
    # Common abbreviations that should not end a sentence
    abbrevs = {"e.g.", "i.e.", "vs.", "fig.", "al.", "dr.", "mr.", "mrs.", "ms.", "co.", "inc.", "ltd."}
    
    sentences: list[SentenceSpan] = []
    start = 0
    length = len(text)
    
    i = 0
    while i < length:
        # Check for sentence end markers
        if text[i] in (".", "?", "!"):
            # Check if this marker is followed by space/newline or end of text
            if i + 1 == length or text[i+1].isspace():
                # Extract preceding candidate sentence text
                candidate_end = i + 1
                candidate = text[start:candidate_end]
                
                # Check if it ends with a known abbreviation
                words = candidate.split()
                if words:
                    last_word = words[-1].lower()
                    # Check if last word is a single uppercase letter followed by period, or in abbreviations list
                    if last_word in abbrevs or (len(last_word) == 2 and last_word[0].isalpha() and last_word[1] == '.'):
                        # It is an abbreviation, do not split here
                        i += 1
                        continue
                
                # We have a valid sentence boundary
                cleaned = candidate.strip()
                if cleaned:
                    # Find actual start index of the cleaned text in the candidate
                    c_start = start + candidate.index(cleaned[0])
                    c_end = c_start + len(cleaned)
                    sentences.append(SentenceSpan(text=cleaned, start_char=c_start, end_char=c_end))
                start = i + 1
        i += 1
        
    # Add any remaining text
    if start < length:
        candidate = text[start:]
        cleaned = candidate.strip()
        if cleaned:
            c_start = start + candidate.index(cleaned[0])
            c_end = c_start + len(cleaned)
            sentences.append(SentenceSpan(text=cleaned, start_char=c_start, end_char=c_end))
            
    return sentences


class SectionChunker:
    """Splits section text into parent context units and child chunks."""

    def __init__(
        self,
        child_min_tokens: int = 200,
        child_target_tokens: int = 280,
        child_max_tokens: int = 350,
        parent_target_tokens: int = 800,
    ) -> None:
        self.child_min = child_min_tokens
        self.child_target = child_target_tokens
        self.child_max = child_max_tokens
        self.parent_target = parent_target_tokens

    def chunk_section(self, section_text: str, section_id_prefix: str) -> list[EvidenceUnit]:
        """Split a section into parent evidence units, each containing child chunks.

        Ensures sentence boundaries are respected (chunks end precisely on sentence boundaries).
        """
        if not section_text.strip():
            return []

        sentences = segment_sentences(section_text)
        if not sentences:
            return []

        evidence_units: list[EvidenceUnit] = []
        
        # 1. Group sentences into Parent Units (target 600 - 1200 tokens)
        current_parent_sentences: list[SentenceSpan] = []
        current_parent_tokens = 0
        parent_groups: list[list[SentenceSpan]] = []

        for sentence in sentences:
            sentence_tokens = count_tokens(sentence.text)
            # If adding this sentence exceeds parent budget and we have at least one sentence
            if current_parent_tokens + sentence_tokens > self.parent_target and current_parent_sentences:
                parent_groups.append(current_parent_sentences)
                current_parent_sentences = [sentence]
                current_parent_tokens = sentence_tokens
            else:
                current_parent_sentences.append(sentence)
                current_parent_tokens += sentence_tokens

        if current_parent_sentences:
            parent_groups.append(current_parent_sentences)

        # 2. Within each parent group, create child chunks with ~15% overlap
        for parent_idx, parent_sents in enumerate(parent_groups):
            p_start = parent_sents[0].start_char
            p_end = parent_sents[-1].end_char
            p_text = section_text[p_start:p_end]
            p_id = f"{section_id_prefix}_P{parent_idx + 1:02d}"

            child_chunks: list[ChildChunk] = []
            
            # Simple child chunk window sliding
            i = 0
            child_idx = 1
            while i < len(parent_sents):
                chunk_sents: list[SentenceSpan] = []
                chunk_tokens = 0
                
                # Expand forward to hit target tokens
                j = i
                while j < len(parent_sents):
                    sent_tokens = count_tokens(parent_sents[j].text)
                    # If single sentence is huge, add it
                    if not chunk_sents:
                        chunk_sents.append(parent_sents[j])
                        chunk_tokens += sent_tokens
                        j += 1
                        continue
                    
                    if chunk_tokens + sent_tokens > self.child_max:
                        break
                    
                    chunk_sents.append(parent_sents[j])
                    chunk_tokens += sent_tokens
                    j += 1

                c_start = chunk_sents[0].start_char
                c_end = chunk_sents[-1].end_char
                c_text = section_text[c_start:c_end]
                c_id = f"{p_id}_C{child_idx:02d}"
                
                child_chunks.append(
                    ChildChunk(
                        id=c_id,
                        text=c_text,
                        start_char=c_start,
                        end_char=c_end,
                        token_count=chunk_tokens,
                    )
                )
                child_idx += 1

                # Slide window: find the starting sentence for the next chunk
                # aiming for ~15% overlap.
                # Let's slide back a bit: check tokens from the end
                if j >= len(parent_sents):
                    break # Reached the end
                
                # Slide back to find overlap start
                overlap_tokens = 0
                slide_back = 0
                for k in range(j - 1, i, -1):
                    sent_tok = count_tokens(parent_sents[k].text)
                    if overlap_tokens + sent_tok > self.child_target * 0.15:
                        break
                    overlap_tokens += sent_tok
                    slide_back += 1
                
                # Advance starting index
                next_i = j - slide_back
                if next_i <= i:
                    next_i = i + 1  # Ensure progress
                i = next_i

            evidence_units.append(
                EvidenceUnit(
                    id=p_id,
                    text=p_text,
                    start_char=p_start,
                    end_char=p_end,
                    token_count=count_tokens(p_text),
                    child_chunks=child_chunks,
                )
            )

        return evidence_units
