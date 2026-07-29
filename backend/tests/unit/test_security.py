import time
import unittest
import sys
from unittest.mock import Mock
from pathlib import Path
from fastapi import HTTPException, status

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.core.rate_limiter import MemoryRateLimiter
from evidence_platform.core.csrf import verify_csrf_token


class TestSecurityUtilities(unittest.TestCase):

    def test_memory_rate_limiter(self):
        limiter = MemoryRateLimiter()
        key = "test_ip_123"

        # Allowed up to 3 requests
        self.assertTrue(limiter.is_allowed(key, limit=3, window=2))
        self.assertTrue(limiter.is_allowed(key, limit=3, window=2))
        self.assertTrue(limiter.is_allowed(key, limit=3, window=2))
        
        # 4th request should be blocked
        self.assertFalse(limiter.is_allowed(key, limit=3, window=2))

        # Sleep to let window expire
        time.sleep(2.1)
        
        # Should be allowed again
        self.assertTrue(limiter.is_allowed(key, limit=3, window=2))

    def test_csrf_safe_methods_allowed(self):
        # GET method should bypass CSRF check
        mock_request = Mock()
        mock_request.method = "GET"
        
        # Verify no exception is raised
        try:
            verify_csrf_token(mock_request)
        except HTTPException:
            self.fail("verify_csrf_token raised HTTPException for safe GET method")

    def test_csrf_matching_tokens_allowed(self):
        mock_request = Mock()
        mock_request.method = "POST"
        mock_request.cookies = {"csrf_token": "token123"}
        mock_request.headers = {"X-CSRF-Token": "token123"}

        try:
            verify_csrf_token(mock_request)
        except HTTPException:
            self.fail("verify_csrf_token raised HTTPException for matching tokens")

    def test_csrf_mismatched_tokens_denied(self):
        mock_request = Mock()
        mock_request.method = "POST"
        mock_request.cookies = {"csrf_token": "token123"}
        mock_request.headers = {"X-CSRF-Token": "different_token"}

        with self.assertRaises(HTTPException) as ctx:
            verify_csrf_token(mock_request)
        
        self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("mismatch", ctx.exception.detail)

    def test_csrf_missing_tokens_denied(self):
        mock_request = Mock()
        mock_request.method = "POST"
        mock_request.cookies = {}
        mock_request.headers = {}

        with self.assertRaises(HTTPException) as ctx:
            verify_csrf_token(mock_request)
            
        self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
