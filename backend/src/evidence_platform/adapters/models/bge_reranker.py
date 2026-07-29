import gc
import logging
import torch

try:
    from FlagEmbedding import FlagReranker
    _HAS_RERANKER = True
except ImportError:
    _HAS_RERANKER = False

from ...ports.models import RerankerClient

# Patch Transformers XLMRobertaModel.__init__ to ignore 'dtype' kwarg passed by FlagEmbedding in transformers >= 4.49
try:
    from transformers.models.xlm_roberta.modeling_xlm_roberta import XLMRobertaModel
    _orig_xlm_init = XLMRobertaModel.__init__
    def _patched_xlm_init(self, *args, **kwargs):
        kwargs.pop("dtype", None)
        return _orig_xlm_init(self, *args, **kwargs)
    XLMRobertaModel.__init__ = _patched_xlm_init
except Exception:
    pass

logger = logging.getLogger("evidence_platform.adapters.bge_reranker")

class BGEM3RerankerClient(RerankerClient):
    """Real BGE-Reranker client using local GPU (CUDA) with memory optimization."""

    _reranker = None

    def __init__(self) -> None:
        if not _HAS_RERANKER:
            raise ImportError("FlagEmbedding library is not installed.")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if BGEM3RerankerClient._reranker is None:
            logger.info(f"Initializing BGE-Reranker model on device: {self.device}")
            BGEM3RerankerClient._reranker = FlagReranker(
                "BAAI/bge-reranker-v2-m3",
                use_fp16=(self.device == "cuda"),
                device=self.device
            )
            logger.info("BGE-Reranker model loaded successfully.")
        self.reranker = BGEM3RerankerClient._reranker

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Compute relevance scores for a list of passages against a query."""
        if not passages:
            return []

        logger.info(f"Reranking {len(passages)} passages against query: {query[:60]}...")
        
        sentence_pairs = [[query, passage] for passage in passages]
        batch_size = 16
        scores = []
        
        with torch.no_grad():
            for i in range(0, len(sentence_pairs), batch_size):
                batch = sentence_pairs[i : i + batch_size]
                batch_scores = self.reranker.compute_score(batch, normalize=True)
                
                if isinstance(batch_scores, list):
                    scores.extend([float(s) for s in batch_scores])
                else:
                    scores.append(float(batch_scores))
                    
        # Release host RAM cache
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return scores
