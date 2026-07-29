import os
import sys
import json
import uuid
import hashlib
import argparse
import traceback
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add backend src to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from evidence_platform.db.models.models import (
    Base, IngestionJob, IngestionRun, CorpusSnapshot,
    Chunk, EvidenceUnit, DocumentSection, DocumentRevision, SourceDocument
)
from evidence_platform.modules.ingestion.importer import CorpusImporter
from evidence_platform.workers.ingestion_worker import IngestionWorker
from evidence_platform.modules.ingestion.handlers import register_ingestion_handlers
from evidence_platform.adapters.models.factory import get_embedding_client
from evidence_platform.adapters.qdrant.qdrant_client import QdrantIndexClient

from sqlalchemy import event

def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.close()

def run_import_and_index(limit: int = 100, skip_existing: bool = True):
    db_url = "sqlite:///c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/evidence_platform.db"
    print(f"Connecting to database: {db_url}")
    engine = create_engine(db_url, connect_args={"timeout": 60})
    event.listen(engine, "connect", set_sqlite_pragma)
    
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    # Create a default corpus snapshot if none exists
    snapshot = session.query(CorpusSnapshot).first()
    if not snapshot:
        snapshot = CorpusSnapshot(
            id="snapshot_1_0_0",
            status="active",
            name="Local MVP Snapshot",
            retrieval_profile_version="1.0.0"
        )
        session.add(snapshot)
        session.commit()
        print("Created default corpus snapshot: snapshot_1_0_0")
    else:
        print(f"Using existing corpus snapshot: {snapshot.id}")

    # Create a default IngestionRun
    run = IngestionRun(
        id=str(uuid.uuid4()),
        external_run_id="run_" + str(uuid.uuid4())[:8],
        status="running",
        manifest_checksum="checksum_abc123"
    )
    session.add(run)
    session.commit()
    print(f"Created IngestionRun: {run.id}")

    json_dir = Path("c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/pmc-pipeline/data/json")
    all_manifest_files = sorted(list(json_dir.glob("*.json")))
    print(f"Found {len(all_manifest_files)} total JSON manifest files on disk.")

    # Get set of already imported PMCID strings
    existing_pmcids = set()
    if skip_existing:
        rows = session.query(SourceDocument.pmcid).filter(SourceDocument.pmcid.isnot(None)).all()
        existing_pmcids = {r[0] for r in rows}
        print(f"Found {len(existing_pmcids)} papers already present in the database.")

    # Filter out already imported files
    files_to_process = []
    for f in all_manifest_files:
        pmcid_guess = f.stem.upper()
        if skip_existing and pmcid_guess in existing_pmcids:
            continue
        files_to_process.append(f)
        if len(files_to_process) >= limit:
            break

    if not files_to_process:
        print("No new papers to import!")
    else:
        print(f"\n--- Ingesting and Chunking {len(files_to_process)} New Papers ---")
        importer = CorpusImporter(session)
        imported_count = 0

        for idx, file_path in enumerate(files_to_process):
            with open(file_path, "r", encoding="utf-8") as f:
                item = json.load(f)
                
            pmcid = item.get("pmcid", file_path.stem.upper())
            if skip_existing and session.query(SourceDocument).filter_by(pmcid=pmcid).first():
                continue

            item["display_rights"] = True
            if "source" not in item:
                item["source"] = "pmc"
            if "canonical_url" not in item:
                item["canonical_url"] = f"https://ncbi.nlm.nih.uk/pmc/articles/{pmcid}/"
            if "retraction_status" not in item:
                item["retraction_status"] = "not_retracted"
            if "content_sha256" not in item:
                title_encoded = item.get("title", "").encode("utf-8")
                item["content_sha256"] = hashlib.sha256(title_encoded).hexdigest()
            if "parser_name" not in item:
                item["parser_name"] = "pmc_jats_parser"
            if "parser_version" not in item:
                item["parser_version"] = "1.0.0"
            if "source_artifact_uri" not in item:
                item["source_artifact_uri"] = f"file:///mock/{pmcid}.xml"
            if "study_type" not in item:
                raw_category = item.get("evidence_category", "other").lower()
                if raw_category in ("rct", "meta_analysis", "systematic_review", "guideline", "observational"):
                    item["study_type"] = raw_category
                elif "random" in raw_category or raw_category == "rct":
                    item["study_type"] = "rct"
                else:
                    item["study_type"] = "other"
            
            if "tables" not in item or not isinstance(item.get("tables"), list):
                item["tables"] = []
            else:
                new_tables = []
                for t in item["tables"]:
                    if isinstance(t, str):
                        new_tables.append({"linearized_text": t, "structured_json": {}})
                    elif isinstance(t, dict):
                        if "linearized_text" not in t:
                            t["linearized_text"] = t.get("text", "")
                        if "structured_json" not in t:
                            t["structured_json"] = {}
                        new_tables.append(t)
                item["tables"] = new_tables

            if "doi" not in item or not item["doi"]:
                item["doi"] = f"10.1002/{pmcid.lower()}"

            if isinstance(item.get("sections"), dict):
                raw_sections = item["sections"]
                new_sections = []
                for s_idx, (kind, text) in enumerate(raw_sections.items()):
                    new_sections.append({
                        "section_path": [kind],
                        "section_kind": kind if kind in ("introduction", "methods", "results", "discussion", "conclusion", "recommendations", "executive_summary", "other") else "other",
                        "ordinal": s_idx + 1,
                        "text": text,
                        "source_locator": {"kind": "xpath", "locator": {"xpath": "/body"}}
                    })
                item["sections"] = new_sections
            elif isinstance(item.get("sections"), list):
                for sec in item["sections"]:
                    sec["source_locator"] = {"kind": "xpath", "locator": {"xpath": "/body"}}

            try:
                doc_id = importer.import_manifest_item(item, corpus_snapshot_id=snapshot.id)
                imported_count += 1
                
                # Queue Ingestion Indexing Job
                existing_job = session.query(IngestionJob).filter_by(document_id=doc_id, job_type="index").first()
                if not existing_job:
                    job = IngestionJob(
                        id=str(uuid.uuid4()),
                        run_id=run.id,
                        document_id=doc_id,
                        job_type="index",
                        status="queued",
                        attempts=0
                    )
                    session.add(job)
                    session.commit()
                else:
                    existing_job.run_id = run.id
                    existing_job.status = "queued"
                    existing_job.attempts = 0
                    session.commit()
                
                if (idx + 1) % 20 == 0 or (idx + 1) == len(files_to_process):
                    print(f"  [{idx + 1}/{len(files_to_process)}] Imported & chunked {pmcid}")
            except Exception as e:
                session.rollback()
                err_msg = traceback.format_exc().encode("ascii", "ignore").decode("ascii")
                print(f"  Error importing {file_path.name}: {e}")

        print(f"Finished chunking phase. {imported_count} new documents added to database.")

    # Initialize Embedding and Qdrant clients
    print("\n--- Running GPU Embedding Worker (BGE-M3 + Local Qdrant) ---")
    embedding_client = get_embedding_client()
    qdrant_client = QdrantIndexClient()

    # Set up Ingestion Worker
    worker = IngestionWorker(db_session_factory=Session)
    register_ingestion_handlers(worker, embedding_client, qdrant_client)

    # Process all queued jobs in multi-paper GPU batches of 25 (~500 chunks per pass)
    processed_count = 0
    failed_count = 0
    while True:
        job_ids = worker.claim_batch_jobs(limit=25)
        if not job_ids:
            break
        success = worker.process_batch_jobs(job_ids)
        if success:
            processed_count += len(job_ids)
        else:
            failed_count += len(job_ids)
        print(f"  [Ultra-Fast GPU Batch] Embedded and indexed {processed_count} paper jobs into Qdrant...")

    print(f"\nIngestion batch complete. Successfully embedded & indexed {processed_count} papers (failed: {failed_count}).")
    session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import and index PMC papers into SQLite DB and Qdrant.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of new papers to import/index in this run.")
    args = parser.parse_args()
    
    run_import_and_index(limit=args.limit)
