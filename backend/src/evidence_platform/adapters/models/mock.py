import hashlib
import re
from typing import Any
from ...ports.models import EmbeddingClient, RerankerClient, LLMClient, EntailmentClient

class MockEmbeddingClient(EmbeddingClient):
    """Deterministic mock embedding client for testing and local development."""

    def _hash_text(self, text: str, dim: int = 1024) -> list[float]:
        # Generate a deterministic vector using MD5 hash
        h = hashlib.md5(text.encode("utf-8")).digest()
        vector = []
        for i in range(dim):
            # Deterministic float between -1.0 and 1.0
            byte_val = h[i % len(h)]
            val = (byte_val / 127.5) - 1.0
            vector.append(round(val, 4))
        return vector

    def _generate_sparse(self, text: str) -> dict[str, Any]:
        # Extract alphanumeric words and generate mock sparse weights
        words = re.findall(r"\w+", text.lower())
        indices = []
        values = []
        
        seen = set()
        for word in words:
            if word in seen:
                continue
            seen.add(word)
            # Map word to a deterministic index between 1 and 100000
            word_hash = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = (word_hash % 100000) + 1
            indices.append(idx)
            # Mock weight based on length and frequency
            weight = min(1.0, len(word) * 0.1)
            values.append(round(weight, 4))
            
        # Ensure Qdrant expects sorted indices
        sorted_pairs = sorted(zip(indices, values))
        if sorted_pairs:
            indices, values = zip(*sorted_pairs)
        else:
            indices, values = [], []
            
        return {"indices": list(indices), "values": list(values)}

    async def embed_query(self, query: str) -> tuple[list[float], dict[str, Any]]:
        dense = self._hash_text(query, dim=1024)
        sparse = self._generate_sparse(query)
        return dense, sparse

    async def embed_documents(self, documents: list[str]) -> list[tuple[list[float], dict[str, Any]]]:
        results = []
        for doc in documents:
            dense = self._hash_text(doc, dim=1024)
            sparse = self._generate_sparse(doc)
            results.append((dense, sparse))
        return results


class MockRerankerClient(RerankerClient):
    """Deterministic mock rerank client scoring based on keyword overlap."""

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        query_words = set(re.findall(r"\w+", query.lower()))
        scores = []
        for passage in passages:
            passage_words = set(re.findall(r"\w+", passage.lower()))
            overlap = len(query_words.intersection(passage_words))
            # Base score on overlap + descending baseline index to break ties
            base_score = overlap / max(1, len(query_words))
            scores.append(round(base_score, 4))
        return scores


class MockLLMClient(LLMClient):
    """Mock LLM client that generates structured grounding-compliant answers."""

    async def generate_answer(self, query: str, evidence_bundle: dict[str, Any]) -> dict[str, Any]:
        # evidence_bundle format: {"snapshot_id": str, "chunks": [{"id": str, "text": str, "evidence_id": str, ...}]}
        chunks = evidence_bundle.get("chunks", [])
        
        # Test for abstention triggers
        if "abstain" in query.lower() or not chunks:
            return {
                "status": "insufficient_evidence",
                "direct_answer_claims": [],
                "evidence_summary_claims": [],
                "limitations_claims": [],
                "abstention_reason": "No matching clinical trial evidence in active corpus snapshot."
            }

        # Synthesize a direct answer using chunks
        direct_claims = []
        limitations_claims = []
        
        for idx, chunk in enumerate(chunks):
            # Extract first sentence or segment of text
            chunk_text = chunk.get("text", "")
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", chunk_text) if s.strip()]
            first_sentence = sentences[0] if sentences else chunk_text[:60]
            
            evidence_id = chunk.get("evidence_id", f"E{idx+1:02d}")
            # Mock a sentence_id (e.g. E01.S1)
            sentence_id = f"{evidence_id}.S1"
            
            claim_text = f"According to study {evidence_id}, {first_sentence}"
            # Ensure it links to the evidence ID
            direct_claims.append({
                "text": claim_text,
                "evidence_ids": [evidence_id],
                "sentence_ids": [sentence_id]
            })
            
            # If the chunk refers to a small population or sample size, add a limitation
            if any(w in chunk_text.lower() for w in ["limit", "small", "n =", "patients"]):
                limitations_claims.append({
                    "text": f"Trial {evidence_id} has limited sample size or population constraints.",
                    "evidence_ids": [evidence_id],
                    "sentence_ids": [sentence_id]
                })

        return {
            "status": "answer",
            "direct_answer_claims": direct_claims,
            "evidence_summary_claims": [
                {
                    "text": f"Summary of {len(chunks)} clinical trials examined for this query.",
                    "evidence_ids": [c.get("evidence_id", f"E{i+1:02d}") for i, c in enumerate(chunks)],
                    "sentence_ids": []
                }
            ],
            "limitations_claims": limitations_claims,
            "abstention_reason": None
        }


class MockEntailmentClient(EntailmentClient):
    """Mock entailment client deciding NLI support based on word similarity."""

    async def check_entailment(self, premise: str, hypothesis: str) -> str:
        if "contradict" in hypothesis.lower() or "not supported" in hypothesis.lower():
            return "contradiction"
            
        # Standard keyword overlap check
        premise_words = set(re.findall(r"\w+", premise.lower()))
        hyp_words = set(re.findall(r"\w+", hypothesis.lower()))
        
        # Strip out direct citations/source numbering like E01, E02 from matching
        hyp_words = {w for w in hyp_words if not re.match(r"^e\d+$", w)}
        
        # If the hypothesis has a high proportion of words in premise, it entails
        overlap = len(hyp_words.intersection(premise_words))
        ratio = overlap / max(1, len(hyp_words))
        
        if ratio >= 0.35:  # Low threshold to make mock generation pass easily
            return "entailment"
        return "neutral"
