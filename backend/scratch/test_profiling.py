import sys, time, uuid, asyncio
sys.path.append("src")
from sqlalchemy import create_engine, event, or_
from sqlalchemy.orm import sessionmaker
from evidence_platform.db.models.models import Base, SourceDocument, DocumentRevision, Chunk, EvidenceUnit, DocumentSection, IngestionJob
from evidence_platform.adapters.models.bge_m3 import BGEM3EmbeddingClient
from evidence_platform.adapters.qdrant.qdrant_client import QdrantIndexClient

def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.close()

db_url = "sqlite:///c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/evidence_platform.db"
engine = create_engine(db_url, connect_args={"timeout": 60})
event.listen(engine, "connect", set_sqlite_pragma)
Session = sessionmaker(bind=engine)

print("\n=================== PIPELINE PROFILING REPORT (25 Papers / ~500 Chunks) ===================", flush=True)

# 1. Measure Reading Chunks from DB
t0 = time.perf_counter()
session = Session()
jobs = session.query(IngestionJob).filter(or_(IngestionJob.status == "queued", IngestionJob.status == "leased")).limit(25).all()

all_chunk_data = []
for job in jobs:
    doc_id = job.document_id
    revision = session.query(DocumentRevision).filter_by(document_id=doc_id).order_by(DocumentRevision.revision_no.desc()).first()
    if not revision: continue
    doc = session.query(SourceDocument).filter_by(id=doc_id).first()
    if not doc: continue
    results = session.query(
        Chunk,
        EvidenceUnit.content_type,
        DocumentSection.section_kind
    ).join(
        EvidenceUnit, Chunk.evidence_unit_id == EvidenceUnit.id
    ).join(
        DocumentSection, EvidenceUnit.section_id == DocumentSection.id
    ).filter(
        DocumentSection.revision_id == revision.id
    ).all()

    if not results: continue
    
    meta = revision.revision_metadata or {}
    for chunk, content_type, section_kind in results:
        payload = {
            "chunk_id": chunk.id,
            "pmcid": doc.pmcid,
            "title": doc.title,
            "section": section_kind,
            "study_type": revision.study_type,
            "evidence_level": "Level 1",
            "publication_year": 2020,
            "journal": doc.source,
            "authors": meta.get("authors", []),
            "doi": doc.doi,
            "mesh": doc.mesh_terms or [],
            "mesh_terms": doc.mesh_terms or [],
            "evidence_unit_id": chunk.evidence_unit_id,
            "document_id": doc.id,
            "document_revision_id": revision.id,
            "corpus_snapshot_id": revision.corpus_snapshot_id,
            "biomarker_terms": [],
            "oncology_subdomain": "oncology",
            "section_kind": section_kind,
            "content_type": content_type,
            "retraction_status": doc.retraction_status,
            "display_rights": doc.display_rights,
            "is_retrievable": True,
            "embedding_version": "1.0.0",
            "schema_version": "1.0.0"
        }
        all_chunk_data.append({"id": chunk.id, "text": chunk.text, "point_id": chunk.qdrant_point_id or str(uuid.uuid4()), "payload": payload, "parser_version": revision.parser_version})

session.close()
t_read = time.perf_counter() - t0

# 2. Tokenizing & GPU Forward Pass (BGE-M3)
t1 = time.perf_counter()
embedder = BGEM3EmbeddingClient()
chunk_texts = [item["text"] for item in all_chunk_data]
embeddings = asyncio.run(embedder.embed_documents(chunk_texts))
t_gpu = time.perf_counter() - t1

# 3. Uploading to Qdrant
t2 = time.perf_counter()
qdrant = QdrantIndexClient()
collections_points = {}
for idx, item in enumerate(all_chunk_data):
    cname = f"evidence_chunks_{item['parser_version'].replace('.', '_')}"
    if cname not in collections_points:
        collections_points[cname] = []
    dense, sparse = embeddings[idx]
    collections_points[cname].append({"point_id": item["point_id"], "dense_vector": dense, "sparse_vector": sparse, "payload": item["payload"]})

for cname, points in collections_points.items():
    qdrant.create_collection_if_missing(cname, dense_dim=1024)
    qdrant.upsert_points(cname, points)
t_qdrant = time.perf_counter() - t2

# 4. Updating SQLite DB Statuses
t3 = time.perf_counter()
session = Session()
chunk_ids = [item["id"] for item in all_chunk_data]
session.query(Chunk).filter(Chunk.id.in_(chunk_ids)).update({"index_status": "indexed"}, synchronize_session=False)
job_ids = [j.id for j in jobs]
session.query(IngestionJob).filter(IngestionJob.id.in_(job_ids)).update({"status": "succeeded"}, synchronize_session=False)
session.commit()
session.close()
t_db = time.perf_counter() - t3

t_total = t_read + t_gpu + t_qdrant + t_db
pct_read = (t_read / t_total) * 100
pct_gpu = (t_gpu / t_total) * 100
pct_qdrant = (t_qdrant / t_total) * 100
pct_db = (t_db / t_total) * 100

print(f"  1. Reading Chunks (DB Read):          {t_read:.3f} s  ({pct_read:.1f}%)", flush=True)
print(f"  2. BGE-M3 GPU Inference & Tokenizing:   {t_gpu:.3f} s  ({pct_gpu:.1f}%)", flush=True)
print(f"  3. Uploading to Qdrant (Disk/Vector):   {t_qdrant:.3f} s  ({pct_qdrant:.1f}%)", flush=True)
print(f"  4. Updating SQLite Job Statuses:      {t_db:.3f} s  ({pct_db:.1f}%)", flush=True)
print(f"  ---------------------------------------------------------------------", flush=True)
print(f"  TOTAL BATCH TIME ({len(jobs)} papers, {len(all_chunk_data)} chunks): {t_total:.3f} s", flush=True)
print(f"=====================================================================================================\n", flush=True)
