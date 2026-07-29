import gc
import logging
from typing import Any
import torch

try:
    from FlagEmbedding import BGEM3FlagModel
    _HAS_BGE = True
except ImportError:
    _HAS_BGE = False

from ...ports.models import EmbeddingClient

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

# Patch FlagEmbedding M3Embedder.encode to prevent passing return_dense/sparse/colbert to HuggingFace tokenizer
try:
    from FlagEmbedding.inference.embedder.encoder_only.m3 import M3Embedder
    def _patched_m3_encode(self, sentences, batch_size=None, max_length=None, return_dense=None, return_sparse=None, return_colbert_vecs=None, **kwargs):
        if batch_size is None: batch_size = self.batch_size
        if max_length is None: max_length = self.passage_max_length
        if return_dense is not None: self.return_dense = return_dense
        if return_sparse is not None: self.return_sparse = return_sparse
        if return_colbert_vecs is not None: self.return_colbert_vecs = return_colbert_vecs
        
        return super(M3Embedder, self).encode(
            sentences,
            batch_size=batch_size,
            max_length=max_length,
            **kwargs
        )
    M3Embedder.encode = _patched_m3_encode
except Exception:
    pass

logger = logging.getLogger("evidence_platform.adapters.bge_m3")

class BGEM3EmbeddingClient(EmbeddingClient):
    """Real BGE-M3 embedding client using local GPU (CUDA) with memory optimization."""

    _model = None

    def __init__(self) -> None:
        if not _HAS_BGE:
            raise ImportError("FlagEmbedding library is not installed.")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if BGEM3EmbeddingClient._model is None:
            logger.info(f"Initializing BGE-M3 model on device: {self.device}")
            BGEM3EmbeddingClient._model = BGEM3FlagModel(
                "BAAI/bge-m3",
                use_fp16=(self.device == "cuda"),
                device=self.device
            )
            logger.info("BGE-M3 model loaded successfully.")
        self.model = BGEM3EmbeddingClient._model

    def _format_sparse(self, sparse_dict: dict[str, Any] | None) -> dict[str, Any]:
        """Convert BGE-M3 sparse output to Qdrant format."""
        if not sparse_dict or not isinstance(sparse_dict, dict):
            return {"indices": [], "values": []}

        indices = []
        values = []
        for k, v in sparse_dict.items():
            try:
                indices.append(int(k))
                values.append(float(v))
            except ValueError:
                continue

        sorted_pairs = sorted(zip(indices, values))
        if sorted_pairs:
            indices, values = zip(*sorted_pairs)
        else:
            indices, values = [], []

        return {"indices": list(indices), "values": list(values)}

    async def embed_query(self, query: str) -> tuple[list[float], dict[str, Any]]:
        """Generate dense 1024-dim and sparse lexical representations for a query."""
        logger.info(f"Generating query embedding (BGE-M3) for query: {query[:60]}...")
        out = self.model.encode(
            [query],
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False
        )

        dense_vecs = out["dense_vecs"]
        lexical_weights = out["lexical_weights"]

        dense_vec = [float(v) for v in dense_vecs[0]]
        sparse_vec = self._format_sparse(lexical_weights[0] if isinstance(lexical_weights, list) else lexical_weights)

        # Release host RAM cache
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return dense_vec, sparse_vec

    async def embed_documents(self, documents: list[str]) -> list[tuple[list[float], dict[str, Any]]]:
        """Generate dense and sparse embeddings for a batch of text passages."""
        if not documents:
            return []

        logger.info(f"Batch embedding {len(documents)} documents using BGE-M3 on {self.device}...")
        out = self.model.encode(
            documents,
            batch_size=16,
            max_length=512,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False
        )

        dense_vecs = out["dense_vecs"]
        lexical_weights = out["lexical_weights"]

        results = []
        for i in range(len(documents)):
            d_vec = [float(v) for v in dense_vecs[i]]
            s_vec = self._format_sparse(lexical_weights[i] if isinstance(lexical_weights, list) and i < len(lexical_weights) else None)
            results.append((d_vec, s_vec))

        # Release host RAM cache
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return results
