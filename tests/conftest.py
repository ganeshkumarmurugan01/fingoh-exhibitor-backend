"""
Shared test fixtures.

Tests use a TestClient with mocked Supabase calls so you do not need
a real Supabase project to run the test suite.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from app.main import app


# ── Mock JWT token ────────────────────────────────────────────────────────────
MOCK_USER_ID  = "test-user-uuid-0001"
MOCK_ORG_ID   = "test-org-uuid-0001"
MOCK_TOKEN    = "mock.jwt.token"

MOCK_USER = {
    "user_id": MOCK_USER_ID,
    "email": "demo@siemens.com",
    "role": "authenticated",
}

MOCK_PROFILE = {
    "id": MOCK_USER_ID,
    "org_id": MOCK_ORG_ID,
    "name": "Demo User",
    "title": "Sales Manager",
    "role": "admin",
    "created_at": "2025-01-01T00:00:00+00:00",
}

MOCK_ORG = {
    "id": MOCK_ORG_ID,
    "name": "Siemens Healthineers",
    "slug": "siemens-healthineers",
    "plan": "free",
    "created_at": "2025-01-01T00:00:00+00:00",
}


@pytest.fixture
def client():
    """
    TestClient with:
    - Auth dependency overridden to return MOCK_USER
    - No real network calls
    """
    from app.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: MOCK_USER

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def mock_db():
    """Patch get_db to return a MagicMock instead of a real Supabase client."""
    with patch("app.database.get_db") as mock:
        db = MagicMock()
        mock.return_value = db
        yield db
