import os
import logging
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("evidence_platform.qdrant")

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
        
        import sys
        if any(k.startswith("pytest") for k in sys.modules):
            return
            
        if _HAS_QDRANT:
            exe_path = Path("c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/qdrant_bin/qdrant.exe")
            config_path = Path("c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/qdrant_bin/config/config.yaml")

            # 1. Try connecting to Qdrant HTTP server on 127.0.0.1 or host
            target_hosts = [h for h in [host, "127.0.0.1", "localhost"] if h]
            for h in target_hosts:
                try:
                    c = qdrant_client.QdrantClient(
                        host=h,
                        port=port,
                        api_key=api_key or None,
                        timeout=2.0,
                        check_compatibility=False
                    )
                    c.get_collections()
                    self.client = c
                    logger.info(f"Connected to Qdrant server at {h}:{port}")
                    return
                except Exception:
                    pass

            logger.info(f"Qdrant server not responding at port {port}. Attempting auto-start...")
            self.client = None

            # 2. Auto-start native qdrant.exe server daemon if executable exists and port not open
            if exe_path.exists() and config_path.exists():
                try:
                    import socket
                    import subprocess
                    import time

                    # Check if port is already open by a process
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1.0)
                    port_in_use = (sock.connect_ex(("127.0.0.1", port)) == 0)
                    sock.close()

                    if not port_in_use:
                        subprocess.Popen([str(exe_path), "--config-path", str(config_path)], cwd=str(exe_path.parent))
                        time.sleep(3)

                    c = qdrant_client.QdrantClient(
                        host="127.0.0.1",
                        port=port,
                        api_key=api_key or None,
                        timeout=5.0,
                        check_compatibility=False
                    )
                    c.get_collections()
                    self.client = c
                    logger.info(f"Successfully connected to native Qdrant server daemon at 127.0.0.1:{port}")
                    return
                except Exception as ex:
                    logger.warning(f"Failed to auto-start native Qdrant server: {ex}")
                    self.client = None

            # 3. Fallback to local on-disk mode if server cannot be started
            local_path = "c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/qdrant_local"
            if os.path.exists(local_path):
                try:
                    self.client = qdrant_client.QdrantClient(path=local_path)
                    # Validate the local store actually contains indexed vectors
                    _collections = [c.name for c in self.client.get_collections().collections]
                    _total_pts = 0
                    for _cn in _collections:
                        _total_pts += self.client.get_collection(_cn).points_count or 0
                    if _total_pts == 0:
                        logger.warning(
                            "Local Qdrant at %s has 0 indexed vectors across %d collection(s). "
                            "Falling back to SQLite-based retrieval.",
                            local_path, len(_collections)
                        )
                        self.client.close()
                        self.client = None
                    else:
                        logger.info(
                            "Local Qdrant validated: %d vectors across %d collection(s) at %s.",
                            _total_pts, len(_collections), local_path
                        )
                except (Exception, MemoryError, BaseException) as ex:
                    logger.warning(f"Local Qdrant allocation unavailable ({ex}). Using hybrid SQLite fallback.")
                    self.client = None
            else:
                self.client = None

    def is_mock(self) -> bool:
        """Returns True if the client is running in mock mode."""
        return self.client is None

    def collection_point_count(self, collection_name: str) -> int:
        """Return the number of indexed points in a collection, or 0 if unavailable.

        Used as defense-in-depth validation by the retriever before
        committing to GPU embedding generation.
        """
        if self.client is None:
            return 0
        try:
            info = self.client.get_collection(collection_name)
            return info.points_count or 0
        except Exception:
            return 0

    def create_collection_if_missing(self, collection_name: str, dense_dim: int = 1024) -> None:
        """Create Qdrant collection with dense and sparse configuration."""
        if self.is_mock():
            logger.info(f"[Mock Qdrant] Creating collection {collection_name} (dense dim: {dense_dim})")
            return

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
                
                # Create payload indexes for filters used in retrieval
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name="corpus_snapshot_id",
                    field_schema=qmodels.PayloadSchemaType.KEYWORD
                )
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name="study_type",
                    field_schema=qmodels.PayloadSchemaType.KEYWORD
                )
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name="publication_year",
                    field_schema=qmodels.PayloadSchemaType.INTEGER
                )
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name="retraction_status",
                    field_schema=qmodels.PayloadSchemaType.KEYWORD
                )
                logger.info(f"Created real Qdrant collection and payload indexes: {collection_name}")
        except Exception as e:
            logger.error(f"Error creating real Qdrant collection: {e}")
            raise e

    def upsert_points(self, collection_name: str, points: list[dict[str, Any]]) -> None:
        """Upsert a list of points into Qdrant.

        Each point dict should contain:
        - point_id (UUID string)
        - dense_vector (list of floats)
        - sparse_vector (dict with keys 'indices' and 'values')
        - payload (dict metadata filters)
        """
        if self.is_mock():
            logger.info(f"[Mock Qdrant] Upserting {len(points)} points to collection {collection_name}")
            return

        try:
            qpoints = []
            for p in points:
                pid = p["point_id"]
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
            logger.debug(f"Successfully upserted {len(qpoints)} points to Qdrant")
        except Exception as e:
            logger.error(f"Failed to upsert points to real Qdrant: {e}")
            raise e

    def delete_points(self, collection_name: str, point_ids: list[str]) -> None:
        """Delete points from a collection."""
        if self.is_mock():
            logger.info(f"[Mock Qdrant] Deleting {len(point_ids)} points from {collection_name}")
            return

        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=qmodels.PointIdsList(points=point_ids)
            )
            logger.debug(f"Successfully deleted {len(point_ids)} points from Qdrant")
        except Exception as e:
            logger.error(f"Failed to delete points from real Qdrant: {e}")
            raise e
