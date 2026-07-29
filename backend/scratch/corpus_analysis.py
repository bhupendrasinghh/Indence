"""Corpus coverage analysis for evidence_platform.db"""
import sys
sys.path.insert(0, 'src')
import sqlite3

DB = 'evidence_platform.db'
conn = sqlite3.connect(DB)
c = conn.cursor()

print("=" * 70)
print("CORPUS COVERAGE ANALYSIS")
print("=" * 70)

# 1. Total documents
c.execute("SELECT COUNT(*) FROM source_documents")
print(f"\n1. Total documents (SourceDocument): {c.fetchone()[0]}")

# 2. Total chunks
c.execute("SELECT COUNT(*) FROM chunks")
print(f"2. Total chunks (Chunk): {c.fetchone()[0]}")

# 3. Study type distribution
print("\n3. Study type distribution (DocumentRevision.study_type):")
c.execute("SELECT study_type, COUNT(*) FROM document_revisions GROUP BY study_type ORDER BY COUNT(*) DESC")
for row in c.fetchall():
    print(f"   {row[0]}: {row[1]}")

# 4. Cervical cancer papers
print("\n4. Cervical cancer papers:")
c.execute("SELECT COUNT(DISTINCT sd.id) FROM source_documents sd WHERE LOWER(sd.title) LIKE '%cervical cancer%'")
print(f"   Documents with 'cervical cancer' in title: {c.fetchone()[0]}")
c.execute("SELECT COUNT(DISTINCT sd.id) FROM source_documents sd WHERE LOWER(sd.title) LIKE '%cervical%'")
print(f"   Documents with 'cervical' in title: {c.fetchone()[0]}")

# 5. FIGO staging mentions
c.execute("SELECT COUNT(*) FROM chunks WHERE text LIKE '%FIGO%'")
print(f"\n5. Chunks mentioning 'FIGO': {c.fetchone()[0]}")

# 6. Stage IB2
c.execute("SELECT COUNT(*) FROM chunks WHERE text LIKE '%IB2%'")
print(f"6. Chunks mentioning 'IB2': {c.fetchone()[0]}")

# 7. Mastectomy
c.execute("SELECT COUNT(*) FROM chunks WHERE LOWER(text) LIKE '%mastectomy%'")
print(f"7. Chunks mentioning 'mastectomy': {c.fetchone()[0]}")

# 8. Breast-conserving
c.execute("SELECT COUNT(*) FROM chunks WHERE LOWER(text) LIKE '%breast-conserving%' OR LOWER(text) LIKE '%lumpectomy%'")
print(f"8. Chunks mentioning 'breast-conserving' or 'lumpectomy': {c.fetchone()[0]}")

# 9. Pub year distribution
print("\n9. Publication year distribution (top years):")
c.execute("""
    SELECT SUBSTR(publication_date, 1, 4) as year, COUNT(*)
    FROM source_documents
    WHERE publication_date IS NOT NULL AND publication_date != ''
    GROUP BY year ORDER BY year DESC LIMIT 15
""")
for row in c.fetchall():
    print(f"   {row[0]}: {row[1]}")

# 10. Qdrant point IDs
c.execute("SELECT COUNT(*) FROM chunks WHERE qdrant_point_id IS NOT NULL AND qdrant_point_id != ''")
with_qdrant = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM chunks WHERE qdrant_point_id IS NULL OR qdrant_point_id = ''")
without_qdrant = c.fetchone()[0]
print(f"\n10. Qdrant point IDs:")
print(f"    Chunks WITH qdrant_point_id: {with_qdrant}")
print(f"    Chunks WITHOUT qdrant_point_id: {without_qdrant}")

# 11. Embedding data
c.execute("SELECT embedding_model, COUNT(*) FROM chunks GROUP BY embedding_model")
print(f"\n11. Embedding model distribution:")
for row in c.fetchall():
    print(f"    {row[0]}: {row[1]}")
c.execute("SELECT index_status, COUNT(*) FROM chunks GROUP BY index_status")
print(f"    Index status distribution:")
for row in c.fetchall():
    print(f"      {row[0]}: {row[1]}")

# 12. Average chunk stats
c.execute("SELECT AVG(LENGTH(text)), MIN(LENGTH(text)), MAX(LENGTH(text)), AVG(token_count) FROM chunks")
row = c.fetchone()
print(f"\n12. Chunk stats:")
print(f"    Avg text length (chars): {row[0]:.1f}")
print(f"    Min: {row[1]}, Max: {row[2]}")
print(f"    Avg token count: {row[3]:.1f}")

# 13. Unique PMCIDs
c.execute("SELECT COUNT(DISTINCT pmcid) FROM source_documents WHERE pmcid IS NOT NULL AND pmcid != ''")
print(f"\n13. Unique PMCIDs: {c.fetchone()[0]}")

# 14. Sample cervical cancer documents
print("\n14. Sample cervical cancer documents:")
c.execute("""
    SELECT sd.title, sd.pmcid, dr.study_type, sd.publication_date
    FROM source_documents sd
    JOIN document_revisions dr ON dr.document_id = sd.id
    WHERE LOWER(sd.title) LIKE '%cervical cancer%'
    LIMIT 15
""")
for row in c.fetchall():
    print(f"    [{row[2]}] {row[0][:80]}... (PMCID: {row[1]}, Date: {row[3]})")

# 15. Sample guideline documents
print("\n15. Sample guideline documents:")
c.execute("""
    SELECT sd.title, sd.pmcid, sd.publication_date
    FROM source_documents sd
    JOIN document_revisions dr ON dr.document_id = sd.id
    WHERE dr.study_type = 'guideline'
    LIMIT 10
""")
for row in c.fetchall():
    print(f"    {row[0][:90]}... (PMCID: {row[1]}, Date: {row[2]})")

# 16. Check if qdrant_local directory exists
import os
qdrant_local_path = "c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/qdrant_local"
if os.path.exists(qdrant_local_path):
    print(f"\n16. Qdrant local directory EXISTS at: {qdrant_local_path}")
    import glob
    files = glob.glob(os.path.join(qdrant_local_path, "**"), recursive=True)
    print(f"    Files in qdrant_local: {len(files)}")
    for f in files[:20]:
        print(f"      {f}")
else:
    print(f"\n16. Qdrant local directory DOES NOT EXIST at: {qdrant_local_path}")

# 17. Revisions and sections counts
c.execute("SELECT COUNT(*) FROM document_revisions")
print(f"\n17. Total document_revisions: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM document_sections")
print(f"    Total document_sections: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM evidence_units")
print(f"    Total evidence_units: {c.fetchone()[0]}")

conn.close()
print("\nDone.")
