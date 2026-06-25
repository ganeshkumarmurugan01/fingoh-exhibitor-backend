from unittest.mock import MagicMock, patch
from tests.conftest import MOCK_ORG_ID

MOCK_STAFF = {
    "id": "staff-uuid-001",
    "org_id": MOCK_ORG_ID,
    "name": "Raj Kumar",
    "email": "raj.kumar@siemens.com",
    "title": "Senior Sales Manager",
    "responsibility": "CT & MRI — large hospital accounts",
    "created_at": "2025-01-01T00:00:00+00:00",
}


def test_list_staff_empty(client):
    with patch("app.routers.staff.get_db") as mock_get_db, \
         patch("app.routers.staff.get_user_org", return_value=MOCK_ORG_ID):
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = []
        mock_get_db.return_value = db

        response = client.get("/api/v1/staff/")
        assert response.status_code == 200
        assert response.json() == []


def test_add_staff_success(client):
    with patch("app.routers.staff.get_db") as mock_get_db, \
         patch("app.routers.staff.get_user_org", return_value=MOCK_ORG_ID):
        db = MagicMock()
        # No duplicate found
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.maybe_single.return_value.execute.return_value.data = None
        # Insert returns new staff
        db.table.return_value.insert.return_value.execute.return_value.data = [MOCK_STAFF]
        mock_get_db.return_value = db

        response = client.post("/api/v1/staff/", json={
            "name": "Raj Kumar",
            "email": "raj.kumar@siemens.com",
            "title": "Senior Sales Manager",
            "responsibility": "CT & MRI — large hospital accounts",
        })
        assert response.status_code == 201
        assert response.json()["name"] == "Raj Kumar"


def test_add_staff_duplicate_email(client):
    with patch("app.routers.staff.get_db") as mock_get_db, \
         patch("app.routers.staff.get_user_org", return_value=MOCK_ORG_ID):
        db = MagicMock()
        # Duplicate found
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.maybe_single.return_value.execute.return_value.data = {"id": "existing"}
        mock_get_db.return_value = db

        response = client.post("/api/v1/staff/", json={
            "name": "Raj Kumar",
            "email": "raj.kumar@siemens.com",
            "title": "Manager",
        })
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]


def test_add_staff_invalid_email(client):
    response = client.post("/api/v1/staff/", json={
        "name": "Raj Kumar",
        "email": "not-an-email",
        "title": "Manager",
    })
    assert response.status_code == 422


def test_staff_login_not_found(client):
    with patch("app.routers.staff.get_db") as mock_get_db:
        db = MagicMock()
        # Event found
        db.table.return_value.select.return_value.eq.return_value \
            .maybe_single.return_value.execute.return_value.data = {"org_id": MOCK_ORG_ID}
        # Staff not found
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.maybe_single.return_value.execute.return_value.data = None
        mock_get_db.return_value = db

        response = client.post("/api/v1/staff/verify-login", json={
            "email": "unknown@company.com",
            "event_id": "evt-001",
        })
        assert response.status_code == 404
