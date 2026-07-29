"""Quick script to query the migrated database and display its contents."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "metadata" / "papers_postgres_mirror.db"

def inspect() -> None:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    print("=" * 65)
    print("MIGRATED POSTGRESQL-MIRROR DATABASE INSPECTION")
    print("=" * 65)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 1. Print Table Row Counts
    tables = [
        "source_documents",
        "document_revisions",
        "document_sections",
        "evidence_units",
        "chunks",
        "evidence_card_data",
    ]
    
    print("\n[1] Table Row Counts:")
    print("-" * 40)
    for t in tables:
        try:
            count = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"  * {t:<22} : {count} rows")
        except sqlite3.OperationalError as e:
            print(f"  * {t:<22} : table missing or error: {e}")

    # 2. Get a sample document that has sections (using INNER JOIN to guarantee data exists)
    print("\n[2] Sample Document Metadata:")
    print("-" * 40)
    query = """
        SELECT d.id, d.source_key, d.title, d.publication_date, d.mesh_terms, r.id as rev_id
        FROM source_documents d
        JOIN document_revisions r ON r.document_id = d.id
        JOIN document_sections s ON s.revision_id = r.id
        LIMIT 1
    """
    doc = conn.execute(query).fetchone()
    
    if not doc:
        print("No documents with sections found.")
        conn.close()
        return

    print(f"  * ID              : {doc['id']}")
    print(f"  * PMCID           : {doc['source_key']}")
    print(f"  * Title           : {doc['title']}")
    print(f"  * Pub Date        : {doc['publication_date']}")
    print(f"  * MeSH Terms      : {doc['mesh_terms']}")

    # 3. Get document sections
    print("\n[3] Sample Sections parsed:")
    print("-" * 40)
    sections = conn.execute(
        f"SELECT id, section_path, section_kind, ordinal, length(text) as txt_len FROM document_sections WHERE revision_id = '{doc['rev_id']}' LIMIT 3"
    ).fetchall()
    
    for s in sections:
        print(f"  * Section Kind: {s['section_kind']:<12} | Ordinal: {s['ordinal']} | Path: {s['section_path']} | Length: {s['txt_len']} chars")

    # 4. Get a sample Parent Context Unit and its Child Chunks
    if sections:
        sample_sec_id = sections[0]["id"]
        print("\n[4] Sample Parent context (Evidence Unit) and Child Chunks:")
        print("-" * 65)
        eu = conn.execute(
            f"SELECT id, ordinal, start_char, end_char, token_count, text FROM evidence_units WHERE section_id = '{sample_sec_id}' LIMIT 1"
        ).fetchone()
        
        if eu:
            print(f"  * Parent (Evidence Unit ID: {eu['id']}):")
            print(f"    - Ordinal     : {eu['ordinal']}")
            print(f"    - Char Span   : {eu['start_char']} -> {eu['end_char']}")
            print(f"    - Token Count : {eu['token_count']}")
            print(f"    - Snippet     : \"{eu['text'][:150].replace('\n', ' ')}...\"")
            
            # Get child chunks
            chunks = conn.execute(
                f"SELECT id, ordinal, start_char, end_char, token_count, text FROM chunks WHERE evidence_unit_id = '{eu['id']}'"
            ).fetchall()
            
            print(f"\n    - Child Chunks ({len(chunks)}):")
            for idx, c in enumerate(chunks):
                print(f"      [{idx+1}] Chunk ID: {c['id']}")
                print(f"          - Ordinal     : {c['ordinal']}")
                print(f"          - Char Span   : {c['start_char']} -> {c['end_char']}")
                print(f"          - Token Count : {c['token_count']}")
                print(f"          - Text        : \"{c['text'][:120].replace('\n', ' ')}...\"")
        else:
            print("No parent evidence units found for this section.")

    conn.close()
    print("\n" + "=" * 65)

if __name__ == "__main__":
    inspect()
