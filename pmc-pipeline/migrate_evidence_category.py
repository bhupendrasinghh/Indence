"""Migration: Add evidence_category column and backfill existing rows.

Run once after upgrading to the guideline-aware pipeline:

    python migrate_evidence_category.py

This script:
1. Adds the evidence_category TEXT column (if not present).
2. Creates the idx_papers_evidence_category index (if not present).
3. Reads publication_types JSON for every row and classifies it.
4. Updates all rows with the derived evidence_category.
"""

import json
import sqlite3
import sys
from pathlib import Path

# Re-use the same classification logic as the pipeline
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.parser import classify_evidence_category

DB_PATH = Path(__file__).resolve().parent / "data" / "metadata" / "papers.db"


def migrate(db_path: str | Path = DB_PATH) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # 1. Add column if missing
    columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "evidence_category" not in columns:
        conn.execute("ALTER TABLE papers ADD COLUMN evidence_category TEXT")
        print("Added column: evidence_category")
    else:
        print("Column evidence_category already exists — skipping ALTER.")

    # 2. Create index if missing
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_papers_evidence_category ON papers(evidence_category)"
    )
    print("Ensured index: idx_papers_evidence_category")

    # 3. Backfill all rows
    rows = conn.execute("SELECT id, publication_types FROM papers").fetchall()
    updated = 0
    for row in rows:
        pub_types_raw = row["publication_types"]
        try:
            pub_types = json.loads(pub_types_raw) if pub_types_raw else []
        except (json.JSONDecodeError, TypeError):
            pub_types = []

        category = classify_evidence_category(pub_types)
        conn.execute(
            "UPDATE papers SET evidence_category = ? WHERE id = ?",
            (category, row["id"]),
        )
        updated += 1

    conn.commit()

    # 4. Report
    stats = conn.execute(
        "SELECT evidence_category, COUNT(*) as cnt FROM papers GROUP BY evidence_category"
    ).fetchall()
    print(f"\nBackfilled {updated} rows:")
    for s in stats:
        print(f"  {s['evidence_category'] or 'NULL'}: {s['cnt']}")

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
