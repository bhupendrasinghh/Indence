import os
import sys
from ...core.config import settings
from ...ports.models import EmbeddingClient, RerankerClient, LLMClient, EntailmentClient
from .mock import MockEmbeddingClient, MockRerankerClient, MockLLMClient, MockEntailmentClient

def get_embedding_client() -> EmbeddingClient:
    """Resolve embedding client based on settings with lazy model loading."""
    if any(k.startswith("pytest") for k in sys.modules):
        return MockEmbeddingClient()
    provider = settings.EMBEDDING_PROVIDER.lower()
    if provider in ("bge-m3", "bge", "real"):
        try:
            from .bge_m3 import BGEM3EmbeddingClient, _HAS_BGE
            if _HAS_BGE:
                return BGEM3EmbeddingClient()
            import logging
            logging.getLogger("evidence_platform.factory").warning(
                "FlagEmbedding library not installed. Falling back to MockEmbeddingClient."
            )
        except Exception as e:
            import logging
            logging.getLogger("evidence_platform.factory").warning(
                "Failed to initialize BGEM3EmbeddingClient (%s). Falling back to MockEmbeddingClient.", e
            )
    return MockEmbeddingClient()

def get_reranker_client() -> RerankerClient:
    """Resolve rerank client based on settings with lazy model loading."""
    if any(k.startswith("pytest") for k in sys.modules):
        return MockRerankerClient()
    provider = settings.RERANKER_PROVIDER.lower()
    if provider in ("bge-reranker", "bge", "real"):
        try:
            from .bge_reranker import BGEM3RerankerClient, _HAS_RERANKER
            if _HAS_RERANKER:
                return BGEM3RerankerClient()
            import logging
            logging.getLogger("evidence_platform.factory").warning(
                "FlagEmbedding library not installed. Falling back to MockRerankerClient."
            )
        except Exception as e:
            import logging
            logging.getLogger("evidence_platform.factory").warning(
                "Failed to initialize BGEM3RerankerClient (%s). Falling back to MockRerankerClient.", e
            )
    return MockRerankerClient()

def get_llm_client() -> LLMClient:
    """Resolve LLM client based on settings with lazy model loading."""
    if any(k.startswith("pytest") for k in sys.modules):
        return MockLLMClient()
    provider = settings.LLM_PROVIDER.lower()
    if provider in ("openrouter", "real") and settings.LLM_API_KEY:
        from .openrouter import OpenRouterLLMClient
        return OpenRouterLLMClient(api_key=settings.LLM_API_KEY, model_name=settings.LLM_MODEL_NAME)
    return MockLLMClient()

def get_entailment_client() -> EntailmentClient:
    """Resolve entailment checker client based on settings with lazy model loading."""
    if any(k.startswith("pytest") for k in sys.modules):
        return MockEntailmentClient()
    provider = settings.ENTAILMENT_PROVIDER.lower()
    if provider in ("openrouter", "real") and settings.LLM_API_KEY:
        from .openrouter import OpenRouterEntailmentClient
        return OpenRouterEntailmentClient(api_key=settings.LLM_API_KEY, model_name=settings.LLM_MODEL_NAME)
    return MockEntailmentClient()
