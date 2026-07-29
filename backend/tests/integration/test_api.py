import os
import unittest
import sys
import uuid
import datetime
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.db.models.models import Base, User, SourceDocument, DocumentRevision, DocumentSection, EvidenceUnit, Chunk
from evidence_platform.app import app, get_db, auth_manager

DB_PATH = "test_evidence_platform.db"
if os.path.exists(DB_PATH):
    try:
        os.remove(DB_PATH)
    except Exception:
        pass

# Override DB dependency in FastAPI app for testing
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db


class TestFastAPIEndpoints(unittest.TestCase):

    def setUp(self):
        # Create schema
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()
        self.client = TestClient(app)

        # Setup test user
        self.user_id = "user-abc"
        self.email = "clinician@indence.org"
        self.password = "DoctorPassword1"
        self.hashed = auth_manager.hash_password(self.password)
        
        self.user = User(
            id=self.user_id,
            email=self.email,
            password_hash=self.hashed,
            role="clinician"
        )
        self.db.add(self.user)

        # Setup chunk data for search matching
        self.doc_id = "doc-1"
        self.rev_id = "rev-1"
        self.sec_id = "sec-1"
        self.eu_id = "eu-1"
        self.chunk_id = "chunk-1"

        self.db.add(SourceDocument(id=self.doc_id, source="pmc", source_key="PMC001", pmcid="PMC001", title="Trastuzumab study", publication_date="2022-01-01"))
        self.db.add(DocumentRevision(id=self.rev_id, document_id=self.doc_id, status="published", parser_version="1", content_sha256="s", study_type="rct", source_artifact_uri="file:///test.xml"))
        self.db.add(DocumentSection(id=self.sec_id, revision_id=self.rev_id, section_path=["Results"], section_kind="results", ordinal=1, text="Trastuzumab deruxtecan works well in breast cancer.", source_locator={"kind": "none", "locator": {}}))
        self.db.add(EvidenceUnit(id=self.eu_id, section_id=self.sec_id, ordinal=1, start_char=0, end_char=50, text="Trastuzumab deruxtecan works well in breast cancer.", token_count=10, content_type="prose"))
        self.db.add(Chunk(
            id=self.chunk_id, evidence_unit_id=self.eu_id, ordinal=1,
            start_char=0, end_char=50, text="Trastuzumab deruxtecan works well in breast cancer.",
            token_count=10, embedding_model="BGE-M3", embedding_version="1.0.0", index_status="indexed"
        ))
        
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove(DB_PATH)
        except Exception:
            pass

    def test_get_csrf_cookie(self):
        response = self.client.get("/api/v1/csrf-token")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrf_token", response.cookies)

    def test_login_logout_lifecycle(self):
        # 1. Login success
        login_payload = {"email": self.email, "password": self.password}
        response = self.client.post("/api/v1/auth/login", json=login_payload)
        
        self.assertEqual(response.status_code, 200)
        self.assertIn("session_token", response.cookies)
        self.assertEqual(response.json()["email"], self.email)

        # 2. Logout clears cookie
        logout_res = self.client.post("/api/v1/auth/logout")
        self.assertEqual(logout_res.status_code, 200)
        # Note: TestClient delete_cookie updates cookies dictionary or deletes it
        self.assertNotIn("session_token", self.client.cookies)

    def test_search_endpoint_secure(self):
        # 1. Accessing search without login fails (401)
        # We pass matching CSRF tokens to bypass CSRF check and hit get_current_user
        res_401 = self.client.post(
            "/api/v1/search",
            json={"query": "T-DXd"},
            headers={"X-CSRF-Token": "temp_token"},
            cookies={"csrf_token": "temp_token"}
        )
        self.assertEqual(res_401.status_code, 401)

        # 2. Login to obtain session cookie
        self.client.post("/api/v1/auth/login", json={"email": self.email, "password": self.password})
        
        # 3. Accessing search without CSRF fails (403)
        res_403 = self.client.post("/api/v1/search", json={"query": "T-DXd"})
        self.assertEqual(res_403.status_code, 403)

        # 4. Get CSRF token
        csrf_res = self.client.get("/api/v1/csrf-token")
        csrf_token = csrf_res.cookies["csrf_token"]

        # 5. Success search with session cookie and CSRF headers
        headers = {"X-CSRF-Token": csrf_token}
        search_payload = {
            "query": "Is T-DXd effective in breast cancer?",
            "filters": {"study_types": ["rct"], "year_from": 2020}
        }
        search_res = self.client.post("/api/v1/search", json=search_payload, headers=headers)
        
        self.assertEqual(search_res.status_code, 200)
        res_data = search_res.json()
        self.assertEqual(res_data["status"], "answer")
        self.assertTrue(len(res_data["direct_answer_claims"]) > 0)
        self.assertIsNotNone(res_data["trace_id"])

        # 6. Retrieve trace audit route
        trace_id = res_data["trace_id"]
        trace_res = self.client.get(f"/api/v1/traces/{trace_id}")
        self.assertEqual(trace_res.status_code, 200)
        self.assertEqual(trace_res.json()["trace_id"], trace_id)


if __name__ == "__main__":
    unittest.main()
