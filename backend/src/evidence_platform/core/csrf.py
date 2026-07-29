import secrets
import logging
from fastapi import Request, HTTPException, status

logger = logging.getLogger("evidence_platform.csrf")

def generate_csrf_token() -> str:
    """Generate a secure CSRF token."""
    return secrets.token_urlsafe(32)

def verify_csrf_token(request: Request) -> None:
    """Dependency that validates Double-Submit Cookie CSRF token matches headers."""
    # CSRF check is not required for safe HTTP methods
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return

    # Extract token from cookie
    cookie_token = request.cookies.get("csrf_token")
    
    # Extract token from header
    header_token = request.headers.get("X-CSRF-Token")

    if header_token:
        if cookie_token and not secrets.compare_digest(cookie_token, header_token):
            logger.warning("CSRF check failed: tokens do not match.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed: token mismatch."
            )
        return

    if not cookie_token:
        logger.warning("CSRF check failed: missing token in cookie or header.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed: missing token."
        )
