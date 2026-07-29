"""Qdrant client adapter for dense+sparse named vector indexing.

Enables robust mock-based execution when qdrant-client is not installed,
and uses the official client in production.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("pmc_pipeline")

try:
    import qdrant_client
    from qdrant_client.http import models as qmodels
    _HAS_QDRANT = True
except ImportError:
    _HAS_QDRANT = False
    qdrant_client = None
    qmodels = None


class QdrantIndexClient:
    """Client adapter to upsert and delete chunks in Qdrant collections."""

    def __init__(self, host: str = "localhost", port: int = 6333, api_key: str | None = None) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key
        self.client = None
        
        if _HAS_QDRANT:
            try:
                self.client = qdrant_client.QdrantClient(
                    host=host,
                    port=port,
                    api_key=api_key
                )
            except Exception as e:
                logger.warning("Could not initialize real Qdrant Client: %s. Using Mock.", e)

    def is_mock(self) -> bool:
        """Returns True if the client is running in mock mode."""
        return self.client is None

    def create_collection_if_missing(self, collection_name: str, dense_dim: int = 1024) -> None:
        """Create Qdrant collection with dense and sparse configuration."""
        if self.is_mock():
            logger.info("[Mock Qdrant] Creating collection %s (dense dim: %d)", collection_name, dense_dim)
            return

        # Real Qdrant collection setup
        try:
            collections = [c.name for c in self.client.get_collections().collections]
            if collection_name not in collections:
                self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config={
                        "dense": qmodels.VectorParams(
                            size=dense_dim,
                            distance=qmodels.Distance.COSINE
                        )
                    },
                    sparse_vectors_config={
                        "sparse": qmodels.SparseVectorParams()
                    }
                )
                logger.info("Created real Qdrant collection: %s", collection_name)
        except Exception as e:
            logger.error("Error creating real Qdrant collection: %s", e)

    def upsert_points(self, collection_name: str, points: list[dict[str, Any]]) -> None:
        """Upsert a list of child chunks into Qdrant.

        Each point item in points list should contain:
        - point_id (UUID string)
        - dense_vector (list of floats)
        - sparse_vector (dict representation of sparse vector: {indices: list[int], values: list[float]})
        - payload (dict metadata filters)
        """
        if self.is_mock():
            logger.info("[Mock Qdrant] Upserting %d points to collection %s", len(points), collection_name)
            return

        try:
            qpoints = []
            for p in points:
                pid = p["point_id"]
                # Convert string UUID to proper UUID object or int
                try:
                    point_id_obj = uuid.UUID(pid)
                except ValueError:
                    point_id_obj = pid

                vectors = {
                    "dense": p["dense_vector"]
                }
                if "sparse_vector" in p:
                    sp = p["sparse_vector"]
                    vectors["sparse"] = qmodels.SparseVector(
                        indices=sp["indices"],
                        values=sp["values"]
                    )

                qpoints.append(
                    qmodels.PointStruct(
                        id=str(point_id_obj),
                        vector=vectors,
                        payload=p["payload"]
                    )
                )

            self.client.upsert(
                collection_name=collection_name,
                points=qpoints
            )
            logger.debug("Successfully upserted %d points to Qdrant", len(qpoints))
        except Exception as e:
            logger.error("Failed to upsert points to real Qdrant: %s", e)

    def delete_points(self, collection_name: str, point_ids: list[str]) -> None:
        """Delete points from a collection."""
        if self.is_mock():
            logger.info("[Mock Qdrant] Deleting %d points from %s", len(point_ids), collection_name)
            return

        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=qmodels.PointIdsList(points=point_ids)
            )
        except Exception as e:
            logger.error("Failed to delete points from real Qdrant: %s", e)
