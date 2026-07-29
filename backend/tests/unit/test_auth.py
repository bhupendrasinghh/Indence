import unittest
import sys
import datetime
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.db.models.models import Base, User, Session as DBSession
from evidence_platform.modules.auth.auth_manager import AuthManager


class TestAuthManager(unittest.TestCase):

    def setUp(self):
        # Database setup
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.auth_manager = AuthManager(session_expiry_days=1)

        # Setup mock user
        self.user_id = "user-123"
        self.email = "doctor@oncology.org"
        self.plaintext = "SecurePassword123"
        self.hashed = self.auth_manager.hash_password(self.plaintext)

        self.user = User(
            id=self.user_id,
            email=self.email,
            password_hash=self.hashed,
            role="reader"
        )
        self.session.add(self.user)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_password_hashing_and_verification(self):
        # Test valid match
        self.assertTrue(self.auth_manager.verify_password(self.hashed, self.plaintext))
        
        # Test mismatch
        self.assertFalse(self.auth_manager.verify_password(self.hashed, "WrongPassword"))

    def test_session_lifecycle(self):
        # 1. Create session
        session_record = self.auth_manager.create_user_session(self.session, self.user_id)
        token = session_record.raw_token
        
        self.assertIsNotNone(token)
        self.assertEqual(session_record.user_id, self.user_id)

        # 2. Verify session (should return user)
        resolved_user = self.auth_manager.verify_user_session(self.session, token)
        self.assertIsNotNone(resolved_user)
        self.assertEqual(resolved_user.id, self.user_id)
        self.assertEqual(resolved_user.email, self.email)

        # 3. Invalidate session
        self.auth_manager.invalidate_user_session(self.session, token)
        
        # 4. Verify again (should return None)
        resolved_user_after = self.auth_manager.verify_user_session(self.session, token)
        self.assertIsNone(resolved_user_after)

    def test_expired_session(self):
        # Create a session expired in the past
        past_expiry = datetime.datetime.utcnow() - datetime.timedelta(hours=5)
        import hashlib
        expired_token_hash = hashlib.sha256(b"expired_token_123").digest()
        
        expired_session = DBSession(
            id="expired-session-id",
            user_id=self.user_id,
            token_hash=expired_token_hash,
            expires_at=past_expiry,
            created_at=datetime.datetime.utcnow() - datetime.timedelta(days=2)
        )
        self.session.add(expired_session)
        self.session.commit()

        # Verifying should return None (and invalidates session)
        resolved = self.auth_manager.verify_user_session(self.session, "expired_token_123")
        self.assertIsNone(resolved)

        # Check that it was deleted from DB
        db_record = self.session.query(DBSession).filter_by(token_hash=expired_token_hash).first()
        self.assertIsNone(db_record)


if __name__ == "__main__":
    unittest.main()
