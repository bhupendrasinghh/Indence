import os
import logging
import json
import re
import httpx
from typing import Any

from ...ports.models import LLMClient, EntailmentClient

logger = logging.getLogger("evidence_platform.openrouter")

class OpenRouterLLMClient(LLMClient):
    """LLM client using OpenRouter API with Nvidia Nemotron-3 Super."""

    def __init__(self, api_key: str, model_name: str = "nvidia/nemotron-3-super-120b-a12b"):
        self.api_key = api_key
        self.model_name = model_name
        self.endpoint = "https://openrouter.ai/api/v1/chat/completions"

    async def generate_answer(self, query: str, evidence_bundle: dict[str, Any]) -> dict[str, Any]:
        # evidence_bundle format: {"snapshot_id": str, "chunks": [{"id": str, "text": str, "evidence_id": str, ...}]}
        chunks = evidence_bundle.get("chunks", [])
        
        if not chunks:
            return {
                "status": "insufficient_evidence",
                "direct_answer_claims": [],
                "evidence_summary_claims": [],
                "limitations_claims": [],
                "abstention_reason": "No matching clinical trial evidence in active corpus snapshot."
            }

        # 1. Format the evidence context with numbered sentences for exact citation mapping
        context_items = []
        for idx, chunk in enumerate(chunks):
            chunk_text = chunk.get("text", "")
            evidence_id = chunk.get("evidence_id", f"E{idx+1:02d}")
            
            # Segment sentences
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", chunk_text) if s.strip()]
            formatted_sentences = []
            for s_idx, sent in enumerate(sentences):
                sent_id = f"{evidence_id}.S{s_idx+1}"
                formatted_sentences.append(f"[{sent_id}] {sent}")
            
            context_items.append(
                f"Evidence Source {evidence_id}:\n" + "\n".join(formatted_sentences)
            )

        context_str = "\n\n".join(context_items)

        # 2. Build system and user prompt with strict JSON formatting instructions
        system_prompt = (
            "You are Indence, an advanced medical AI assistant specializing in clinical oncology. Your goal is to synthesize a structured, evidence-grounded answer to a clinician's query using ONLY the provided evidence source extracts.\n\n"
            "### RULES\n"
            "1. Grounding Invariant: Every claim in \"direct_answer_claims\" and \"limitations_claims\" MUST be explicitly supported by the evidence extracts. Never invent any facts, extrapolate beyond the text, or cite external knowledge not present in the context.\n"
            "2. Fine-grained Citations: For each claim, you must associate it with:\n"
            "   - \"evidence_ids\": The source ID(s) (e.g., [\"E01\"]) that support the claim.\n"
            "   - \"sentence_ids\": The specific sentence ID(s) (e.g., [\"E01.S2\", \"E01.S3\"]) from the source that contain the exact facts.\n"
            "3. Limitations: Scan the evidence for constraints (small sample size, low patient cohort, country restrictions, conflict of interest, short follow-up time). Document these in \"limitations_claims\".\n"
            "4. Abstention Protocol: If the evidence does not contain sufficient facts to answer the clinician's query, or if the sources are completely irrelevant, you MUST set \"status\" to \"insufficient_evidence\", leave \"direct_answer_claims\" empty, and populate \"abstention_reason\" with a helpful message explaining the gap.\n"
            "5. Strict JSON Output: You must return ONLY a raw JSON object matching the schema below. Do not output conversational text outside the JSON.\n\n"
            "### OUTPUT JSON SCHEMA\n"
            "{\n"
            '  "status": "answer" | "insufficient_evidence",\n'
            '  "direct_answer_claims": [\n'
            "    {\n"
            '      "text": "The factual claim statement supported by the source sentences.",\n'
            '      "evidence_ids": ["E01"],\n'
            '      "sentence_ids": ["E01.S2"]\n'
            "    }\n"
            "  ],\n"
            '  "evidence_summary_claims": [\n'
            "    {\n"
            '      "text": "A brief overall summary of the clinical findings across all sources.",\n'
            '      "evidence_ids": ["E01", "E02"],\n'
            '      "sentence_ids": []\n'
            "    }\n"
            "  ],\n"
            '  "limitations_claims": [\n'
            "    {\n"
            '      "text": "Description of any study limitations found in the evidence (e.g. small sample size).",\n'
            '      "evidence_ids": ["E01"],\n'
            '      "sentence_ids": ["E01.S4"]\n'
            "    }\n"
            "  ],\n"
            '  "abstention_reason": "Explanation of the evidence gap if status is \'insufficient_evidence\', else null"\n'
            "}"
        )

        user_content = (
            f"Clinician Query:\n{query}\n\n"
            f"Evidence Context:\n{context_str}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/indence-rag",
            "X-Title": "Indence RAG Platform"
        }

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 2048
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                if response.status_code != 200:
                    logger.error(f"OpenRouter returned status {response.status_code}: {response.text}")
                    return await self._fallback_answer(query, chunks, f"OpenRouter API error: {response.text}")
                
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"].strip()
                
                # Robustly extract JSON object from response (handling markdown ```json blocks)
                cleaned = content
                if "```" in cleaned:
                    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
                    cleaned = re.sub(r"```$", "", cleaned, flags=re.MULTILINE).strip()
                
                match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
                if match:
                    cleaned = match.group(1)
                
                parsed_json = json.loads(cleaned)
                return parsed_json
        except Exception as e:
            logger.error(f"Failed to generate answer via OpenRouter: {e}")
            return await self._fallback_answer(query, chunks, f"Error calling OpenRouter: {str(e)}")

    async def _fallback_answer(self, query: str, chunks: list[dict], error_msg: str) -> dict[str, Any]:
        """Gracefully fall back to local structured evidence synthesis if LLM API is unreachable."""
        from .mock import MockLLMClient
        logger.warning(f"OpenRouter API call failed ({error_msg}). Falling back to local evidence synthesis.")
        mock = MockLLMClient()
        return await mock.generate_answer(query, {"chunks": chunks})


class OpenRouterEntailmentClient(EntailmentClient):
    """Entailment checker using OpenRouter API with Nvidia Nemotron-3 Super."""

    def __init__(self, api_key: str, model_name: str | None = None):
        self.api_key = api_key
        # Use fast, low-latency classification model for NLI entailment unless explicitly specified
        self.model_name = model_name if model_name and "nemotron" not in model_name else os.environ.get("ENTAILMENT_MODEL_NAME", "meta-llama/llama-3.3-70b-instruct")
        self.endpoint = "https://openrouter.ai/api/v1/chat/completions"

    async def check_entailment(self, premise: str, hypothesis: str) -> str:
        system_prompt = (
            "You are a clinical evidence validator. Compare the premise and hypothesis and output exactly one label: 'entailment', 'contradiction', or 'neutral'.\n"
            "Rules:\n"
            "- Output 'entailment' if the hypothesis is fully supported and justified by the premise.\n"
            "- Output 'contradiction' if the hypothesis directly contradicts or denies the premise.\n"
            "- Output 'neutral' if the premise does not provide enough information to confirm or deny the hypothesis.\n"
            "Return ONLY the single word label, lowercase, with no punctuation or other text."
        )

        user_content = (
            f"Premise (Source Text):\n{premise}\n\n"
            f"Hypothesis (Claim):\n{hypothesis}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/indence-rag",
            "X-Title": "Indence RAG Platform"
        }

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.0,
            "max_tokens": 100
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                if response.status_code != 200:
                    logger.error(f"OpenRouter entailment returned status {response.status_code}: {response.text}")
                    return "neutral"
                
                res_data = response.json()
                label = res_data["choices"][0]["message"]["content"].strip().lower()
                
                # Sanitize response to match exact strings
                if "entailment" in label:
                    return "entailment"
                elif "contradiction" in label:
                    return "contradiction"
                else:
                    return "neutral"
        except Exception as e:
            logger.error(f"Failed to check entailment via OpenRouter: {e}")
            return "neutral"
