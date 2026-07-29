import secrets
import logging
import datetime
import hashlib
from typing import Any, Optional
from sqlalchemy.orm import Session
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from ...db.models.models import User, Session as DBSession

logger = logging.getLogger("evidence_platform.auth")

class AuthManager:
    """Manages password hashing, validation, and database session tokens."""

    def __init__(self, session_expiry_days: int = 7) -> None:
        self.ph = PasswordHasher()
        self.session_expiry_days = session_expiry_days

    def _hash_token(self, token: str) -> bytes:
        """Hash opaque token with SHA-256 to yield bytes for storage."""
        return hashlib.sha256(token.encode("utf-8")).digest()

    def hash_password(self, password: str) -> str:
        """Hash a plaintext password using Argon2."""
        return self.ph.hash(password)

    def verify_password(self, password_hash: str, password_plaintext: str) -> bool:
        """Verify password matches hash. Returns True if valid."""
        try:
            return self.ph.verify(password_hash, password_plaintext)
        except VerifyMismatchError:
            return False
        except Exception as e:
            logger.error(f"Argon2 verification error: {e}")
            return False

    def create_user_session(self, db: Session, user_id: str) -> DBSession:
        """Create a new opaque session token in the database."""
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=self.session_expiry_days)
        
        session = DBSession(
            id=str(secrets.token_hex(16)),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=datetime.datetime.utcnow()
        )
        db.add(session)
        db.commit()
        # Set raw token dynamically so it is accessible to callers
        session.raw_token = token
        return session

    def verify_user_session(self, db: Session, token: str) -> Optional[User]:
        """Verify if a session token is valid and returns the associated User."""
        token_hash = self._hash_token(token)
        session = db.query(DBSession).filter_by(token_hash=token_hash).first()
        if not session:
            return None

        # Check expiration
        if session.expires_at < datetime.datetime.utcnow():
            logger.info(f"Session {session.id} expired. Invalidating.")
            self.invalidate_user_session(db, token)
            return None

        # Resolve user
        user = db.query(User).filter_by(id=session.user_id).first()
        return user

    def invalidate_user_session(self, db: Session, token: str) -> None:
        """Delete/invalidate session token."""
        token_hash = self._hash_token(token)
        session = db.query(DBSession).filter_by(token_hash=token_hash).first()
        if session:
            db.delete(session)
            db.commit()
            logger.info(f"Invalidated session: {session.id}")
