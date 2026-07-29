import logging
from typing import Any
from sqlalchemy.orm import Session
from ...db.models.models import IngestionJob, DocumentRevision, Chunk, SourceDocument
from ...ports.models import EmbeddingClient
from ...adapters.qdrant.qdrant_client import QdrantIndexClient
from ...core.config import settings

logger = logging.getLogger("evidence_platform.handlers")

def register_ingestion_handlers(
    worker: Any,
    embedding_client: EmbeddingClient,
    qdrant_client: QdrantIndexClient
) -> None:
    """Register all ingestion job handlers on the worker daemon."""

    def index_job_handler(job: IngestionJob, session: Session) -> None:
        # 1. Fetch document revision and related chunks
        doc_id = job.document_id
        
        # Get latest active revision
        revision = session.query(DocumentRevision).filter(
            DocumentRevision.document_id == doc_id,
            DocumentRevision.status != "superseded"
        ).order_by(DocumentRevision.revision_no.desc()).first()
        
        if not revision:
            raise ValueError(f"No active DocumentRevision found for document {doc_id}")

        # Get parent source document
        doc = session.query(SourceDocument).filter_by(id=doc_id).first()
        if not doc:
            raise ValueError(f"No SourceDocument found with ID {doc_id}")

        # Get all chunks for this revision
        # Join Chunk -> EvidenceUnit -> DocumentSection -> DocumentRevision
        from ...db.models.models import EvidenceUnit, DocumentSection
        chunks = session.query(Chunk).join(
            EvidenceUnit, Chunk.evidence_unit_id == EvidenceUnit.id
        ).join(
            DocumentSection, EvidenceUnit.section_id == DocumentSection.id
        ).filter(
            DocumentSection.revision_id == revision.id
        ).all()

        if not chunks:
            logger.info(f"No chunks found for document revision {revision.id}. Nothing to index.")
            return

        # Prepare chunk data and payload metadata in a quick read pass
        chunk_data = []
        pub_year = 2020
        if doc.publication_date and len(doc.publication_date) >= 4:
            try:
                pub_year = int(doc.publication_date[:4])
            except ValueError:
                pass

        meta = revision.revision_metadata or {}
        authors = meta.get("authors", meta.get("author_list", []))
        journal = meta.get("journal", doc.source)
        evidence_level = meta.get("evidence_level")
        if not evidence_level:
            st = (revision.study_type or "").lower()
            if st in ("rct", "meta_analysis", "systematic_review"):
                evidence_level = "Level 1"
            elif st in ("guideline", "observational"):
                evidence_level = "Level 2"
            else:
                evidence_level = "Level 3"

        for chunk in chunks:
            from ...db.models.models import EvidenceUnit
            eu = session.query(EvidenceUnit).filter_by(id=chunk.evidence_unit_id).first()
            content_type = eu.content_type if eu else "prose"
            sec = session.query(DocumentSection).filter_by(id=eu.section_id).first() if eu else None
            section_kind = sec.section_kind if sec else "other"

            payload = {
                "chunk_id": chunk.id,
                "pmcid": doc.pmcid,
                "title": doc.title,
                "section": section_kind,
                "study_type": revision.study_type,
                "evidence_level": evidence_level,
                "publication_year": pub_year,
                "journal": journal,
                "authors": authors,
                "doi": doc.doi,
                "mesh": doc.mesh_terms or [],
                "mesh_terms": doc.mesh_terms or [],
                "evidence_unit_id": chunk.evidence_unit_id,
                "document_id": doc.id,
                "document_revision_id": revision.id,
                "corpus_snapshot_id": revision.corpus_snapshot_id,
                "biomarker_terms": meta.get("biomarkers", []),
                "oncology_subdomain": meta.get("subdomain", "oncology"),
                "section_kind": section_kind,
                "content_type": content_type,
                "retraction_status": doc.retraction_status,
                "display_rights": doc.display_rights,
                "is_retrievable": (revision.status == "published" and doc.retraction_status == "not_retracted"),
                "embedding_version": chunk.embedding_version or "1.0.0",
                "schema_version": "1.0.0"
            }

            chunk_data.append({
                "id": chunk.id,
                "text": chunk.text,
                "point_id": chunk.qdrant_point_id or str(uuid.uuid4()),
                "payload": payload
            })

        collection_name = f"evidence_chunks_{revision.parser_version.replace('.', '_')}"
        
        # 2. RELEASE SQLite read transaction before running heavy GPU inference
        session.commit()

        # 3. Call GPU embedding client (NO DB LOCK HELD)
        chunk_texts = [item["text"] for item in chunk_data]
        import asyncio
        embeddings = asyncio.run(embedding_client.embed_documents(chunk_texts))

        if len(embeddings) != len(chunk_data):
            raise ValueError(f"Embedding count mismatch. Expected {len(chunk_data)}, got {len(embeddings)}")

        # 4. Construct Qdrant points with payload
        qdrant_client.create_collection_if_missing(collection_name, dense_dim=1024)
        points = []
        for idx, item in enumerate(chunk_data):
            dense, sparse = embeddings[idx]
            points.append({
                "point_id": item["point_id"],
                "dense_vector": dense,
                "sparse_vector": sparse,
                "payload": item["payload"]
            })

        # 5. Upsert points to Qdrant
        qdrant_client.upsert_points(collection_name, points)

        # 6. Quick 1-ms DB update to mark chunks as indexed
        chunk_ids = [item["id"] for item in chunk_data]
        session.query(Chunk).filter(Chunk.id.in_(chunk_ids)).update({"index_status": "indexed"}, synchronize_session=False)
        session.commit()
            
        logger.info(f"Indexed {len(chunk_data)} chunks in Qdrant and updated database.")

    def index_batch_jobs_handler(jobs: list[IngestionJob], session: Session) -> None:
        """Process a batch of multiple document indexing jobs in a single GPU pass."""
        if not jobs:
            return

        all_chunk_data = []
        doc_chunk_map = {}

        import time
        t_start_read = time.perf_counter()

        doc_ids = [job.document_id for job in jobs]

        # 1. Fetch all active revisions and documents for batch in 2 indexed queries
        revisions = session.query(DocumentRevision).filter(
            DocumentRevision.document_id.in_(doc_ids),
            DocumentRevision.status != "superseded"
        ).all()
        if not revisions:
            session.commit()
            return
        
        rev_map = {r.document_id: r for r in revisions}
        rev_ids = [r.id for r in revisions]
        rev_doc_map = {r.id: r for r in revisions}

        docs = session.query(SourceDocument).filter(SourceDocument.id.in_(doc_ids)).all()
        doc_map = {d.id: d for d in docs}

        # 2. Fetch ALL chunks for all papers in 1 single indexed JOIN query
        from ...db.models.models import EvidenceUnit, DocumentSection
        results = session.query(
            Chunk,
            EvidenceUnit.content_type,
            DocumentSection.section_kind,
            DocumentSection.revision_id
        ).join(
            EvidenceUnit, Chunk.evidence_unit_id == EvidenceUnit.id
        ).join(
            DocumentSection, EvidenceUnit.section_id == DocumentSection.id
        ).filter(
            DocumentSection.revision_id.in_(rev_ids)
        ).all()

        if not results:
            session.commit()
            return

        for chunk, content_type, section_kind, revision_id in results:
            revision = rev_doc_map.get(revision_id)
            if not revision:
                continue
            doc = doc_map.get(revision.document_id)
            if not doc:
                continue

            pub_year = 2020
            if doc.publication_date and len(doc.publication_date) >= 4:
                try:
                    pub_year = int(doc.publication_date[:4])
                except ValueError:
                    pass

            meta = revision.revision_metadata or {}
            authors = meta.get("authors", meta.get("author_list", []))
            journal = meta.get("journal", doc.source)
            evidence_level = meta.get("evidence_level")
            if not evidence_level:
                st = (revision.study_type or "").lower()
                if st in ("rct", "meta_analysis", "systematic_review"):
                    evidence_level = "Level 1"
                elif st in ("guideline", "observational"):
                    evidence_level = "Level 2"
                else:
                    evidence_level = "Level 3"

            payload = {
                "chunk_id": chunk.id,
                "pmcid": doc.pmcid,
                "title": doc.title,
                "section": section_kind,
                "study_type": revision.study_type,
                "evidence_level": evidence_level,
                "publication_year": pub_year,
                "journal": journal,
                "authors": authors,
                "doi": doc.doi,
                "mesh": doc.mesh_terms or [],
                "mesh_terms": doc.mesh_terms or [],
                "evidence_unit_id": chunk.evidence_unit_id,
                "document_id": doc.id,
                "document_revision_id": revision.id,
                "corpus_snapshot_id": revision.corpus_snapshot_id,
                "biomarker_terms": meta.get("biomarkers", []),
                "oncology_subdomain": meta.get("subdomain", "oncology"),
                "section_kind": section_kind,
                "content_type": content_type,
                "retraction_status": doc.retraction_status,
                "display_rights": doc.display_rights,
                "is_retrievable": (revision.status == "published" and doc.retraction_status == "not_retracted"),
                "embedding_version": chunk.embedding_version or "1.0.0",
                "schema_version": "1.0.0"
            }

            all_chunk_data.append({
                "id": chunk.id,
                "text": chunk.text,
                "point_id": chunk.qdrant_point_id or str(uuid.uuid4()),
                "payload": payload,
                "parser_version": revision.parser_version
            })

        if not all_chunk_data:
            session.commit()
            return

        session.commit()
        t_read_sec = time.perf_counter() - t_start_read

        # 2. Call GPU embedding client (Measure BGE-M3 + Tokenization time)
        t_start_gpu = time.perf_counter()
        chunk_texts = [item["text"] for item in all_chunk_data]
        import asyncio
        embeddings = asyncio.run(embedding_client.embed_documents(chunk_texts))
        t_gpu_sec = time.perf_counter() - t_start_gpu

        if len(embeddings) != len(all_chunk_data):
            raise ValueError(f"Embedding count mismatch. Expected {len(all_chunk_data)}, got {len(embeddings)}")

        # 3. Uploading to Qdrant
        t_start_qdrant = time.perf_counter()
        collections_points = {}
        for idx, item in enumerate(all_chunk_data):
            cname = f"evidence_chunks_{item['parser_version'].replace('.', '_')}"
            if cname not in collections_points:
                collections_points[cname] = []
            dense, sparse = embeddings[idx]
            collections_points[cname].append({
                "point_id": item["point_id"],
                "dense_vector": dense,
                "sparse_vector": sparse,
                "payload": item["payload"]
            })

        for cname, points in collections_points.items():
            qdrant_client.create_collection_if_missing(cname, dense_dim=1024)
            qdrant_client.upsert_points(cname, points)
        t_qdrant_sec = time.perf_counter() - t_start_qdrant

        # 4. Updating SQLite DB statuses
        t_start_db = time.perf_counter()
        chunk_ids = [item["id"] for item in all_chunk_data]
        session.query(Chunk).filter(Chunk.id.in_(chunk_ids)).update({"index_status": "indexed"}, synchronize_session=False)
        session.commit()
        t_db_sec = time.perf_counter() - t_start_db

        t_total = t_read_sec + t_gpu_sec + t_qdrant_sec + t_db_sec
        pct_read = (t_read_sec / t_total) * 100
        pct_gpu = (t_gpu_sec / t_total) * 100
        pct_qdrant = (t_qdrant_sec / t_total) * 100
        pct_db = (t_db_sec / t_total) * 100

        print(f"\n=================== PIPELINE PROFILING REPORT ({len(jobs)} papers, {len(all_chunk_data)} chunks) ===================", flush=True)
        print(f"  1. Reading Chunks (DB Read):        {t_read_sec:.3f} s  ({pct_read:.1f}%)", flush=True)
        print(f"  2. BGE-M3 GPU Inference & Tokenizing: {t_gpu_sec:.3f} s  ({pct_gpu:.1f}%)", flush=True)
        print(f"  3. Uploading to Qdrant (Disk/Vector): {t_qdrant_sec:.3f} s  ({pct_qdrant:.1f}%)", flush=True)
        print(f"  4. Updating SQLite Job Statuses:    {t_db_sec:.3f} s  ({pct_db:.1f}%)", flush=True)
        print(f"  TOTAL BATCH TIME:                  {t_total:.3f} s", flush=True)
        print(f"=====================================================================================================\n", flush=True)

        logger.info(f"Batch indexed {len(all_chunk_data)} chunks across {len(jobs)} papers in Qdrant and updated database.")

    # Register handlers
    worker.register_handler("index", index_job_handler)
    worker.index_batch_jobs_handler = index_batch_jobs_handler
