import os
import sys
import uuid
import datetime
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add src to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from evidence_platform.db.models.models import Base, User, SourceDocument, DocumentRevision, DocumentSection, EvidenceUnit, Chunk
from evidence_platform.modules.auth.auth_manager import AuthManager

def seed_database():
    db_url = "sqlite:///c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/evidence_platform.db"
    print(f"Connecting to database: {db_url}")
    engine = create_engine(db_url)
    
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    # 1. Create default Clinician User
    auth = AuthManager()
    existing_user = session.query(User).filter_by(email="clinician@indence.org").first()
    if not existing_user:
        hashed = auth.hash_password("DoctorPassword1")
        user = User(
            id=str(uuid.uuid4()),
            email="clinician@indence.org",
            password_hash=hashed,
            role="clinician"
        )
        session.add(user)
        print("Created default user: clinician@indence.org / DoctorPassword1")
    else:
        print("Default user already exists.")

    # 2. Create sample literature documents (Oncology)
    doc_count = session.query(SourceDocument).count()
    if doc_count == 0:
        doc_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        sec_id = str(uuid.uuid4())
        eu_id = str(uuid.uuid4())
        chunk_id = "chunk-seed-1"

        doc = SourceDocument(
            id=doc_id,
            source="pmc",
            source_key="PMC1010101",
            pmcid="PMC1010101",
            title="Trastuzumab Deruxtecan in HER2-Positive Breast Cancer",
            publication_date="2022-03-24",
            display_rights=True,
            retraction_status="not_retracted"
        )
        session.add(doc)

        revision = DocumentRevision(
            id=rev_id,
            document_id=doc_id,
            status="published",
            parser_version="1.0.0",
            content_sha256="abc123sha",
            study_type="rct",
            source_artifact_uri="file:///mock/literature/PMC1010101.xml"
        )
        session.add(revision)

        section = DocumentSection(
            id=sec_id,
            revision_id=rev_id,
            section_path=["Results", "Efficacy"],
            section_kind="results",
            ordinal=1,
            text="In patients with HER2-positive breast cancer, median progression-free survival (PFS) was 28.8 months with trastuzumab deruxtecan. In comparison, PFS was only 6.8 months with chemotherapy. Safety analysis showed acceptable tolerability.",
            source_locator={"kind": "none", "locator": {}}
        )
        session.add(section)

        # Trastuzumab deruxtecan works well in breast cancer.
        eu = EvidenceUnit(
            id=eu_id,
            section_id=sec_id,
            ordinal=1,
            start_char=0,
            end_char=178,
            text="In patients with HER2-positive breast cancer, median progression-free survival (PFS) was 28.8 months with trastuzumab deruxtecan. In comparison, PFS was only 6.8 months with chemotherapy.",
            token_count=35,
            content_type="prose"
        )
        session.add(eu)

        chunk = Chunk(
            id=chunk_id,
            evidence_unit_id=eu_id,
            ordinal=1,
            start_char=0,
            end_char=178,
            text="In patients with HER2-positive breast cancer, median progression-free survival (PFS) was 28.8 months with trastuzumab deruxtecan. In comparison, PFS was only 6.8 months with chemotherapy.",
            token_count=35,
            embedding_model="BGE-M3",
            embedding_version="1.0.0",
            index_status="indexed"
        )
        session.add(chunk)

        session.commit()
        print("Successfully seeded database with clinical breast cancer oncology documents!")
    else:
        print("Literature documents already exist in the database.")

    session.close()

if __name__ == "__main__":
    seed_database()
