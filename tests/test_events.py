from unittest.mock import MagicMock, patch
from tests.conftest import MOCK_USER_ID, MOCK_ORG_ID

MOCK_EVENT = {
    "id": "evt-uuid-001",
    "org_id": MOCK_ORG_ID,
    "name": "MedTech Asia 2025",
    "type": "medtech",
    "type_label": "MedTech & Healthcare",
    "date_from": "2025-10-15",
    "date_to": "2025-10-17",
    "venue": "Marina Bay Sands Expo, Singapore",
    "country": "Singapore",
    "company": "Siemens Healthineers",
    "product": "Diagnostic imaging & AI-powered radiology",
    "website": "https://siemens-healthineers.com",
    "booth_size": "18",
    "status": "active",
    "created_by": MOCK_USER_ID,
    "created_at": "2025-01-01T00:00:00+00:00",
    "updated_at": "2025-01-01T00:00:00+00:00",
}

CREATE_PAYLOAD = {
    "name": "MedTech Asia 2025",
    "type": "medtech",
    "type_label": "MedTech & Healthcare",
    "date_from": "2025-10-15",
    "date_to": "2025-10-17",
    "venue": "Marina Bay Sands Expo, Singapore",
    "country": "Singapore",
    "company": "Siemens Healthineers",
    "product": "Diagnostic imaging & AI-powered radiology",
    "categories": ["Medical Devices", "Diagnostics"],
    "icp_roles": ["VP / Director", "Procurement Manager"],
    "icp_company_sizes": ["1000+ employees"],
    "icp_visit_reasons": ["Active sourcing / procurement"],
    "intent_why": "We are launching our new AI-powered CT scanner.",
    "intent_buyers": "Hospital procurement directors with active Q1 budgets.",
    "intent_signals": [{"icon": "🚀", "label": "Product launch"}],
    "buyer_signals": [{"icon": "🏥", "label": "Healthcare providers"}],
}


def make_db_mock(event=MOCK_EVENT):
    """Build a DB mock that returns sensible data for event queries."""
    db = MagicMock()

    # profiles query for get_user_org
    profile_chain = MagicMock()
    profile_chain.execute.return_value.data = {"org_id": MOCK_ORG_ID}
    db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value = profile_chain

    # events insert
    events_insert = MagicMock()
    events_insert.execute.return_value.data = [event]

    # categories/icp/intent inserts
    generic_insert = MagicMock()
    generic_insert.execute.return_value.data = [{}]

    # categories/icp/intent selects (for _build_event_detail)
    cats_chain = MagicMock()
    cats_chain.execute.return_value.data = [{"category": "Medical Devices"}]
    icp_chain = MagicMock()
    icp_chain.execute.return_value.data = {"roles": ["VP / Director"]}
    intent_chain = MagicMock()
    intent_chain.execute.return_value.data = {"intent_why": "Launch CT scanner"}

    return db


def test_list_events_empty(client):
    with patch("app.routers.events.get_db") as mock_get_db, \
         patch("app.routers.events.get_user_org", return_value=MOCK_ORG_ID):
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = []
        mock_get_db.return_value = db

        response = client.get("/api/v1/events/")
        assert response.status_code == 200
        assert response.json() == []


def test_list_events_returns_data(client):
    with patch("app.routers.events.get_db") as mock_get_db, \
         patch("app.routers.events.get_user_org", return_value=MOCK_ORG_ID):
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = [MOCK_EVENT]
        mock_get_db.return_value = db

        response = client.get("/api/v1/events/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "MedTech Asia 2025"


def test_create_event_validates_dates(client):
    """date_to before date_from should return 422."""
    bad_payload = {**CREATE_PAYLOAD, "date_from": "2025-10-20", "date_to": "2025-10-15"}
    with patch("app.routers.events.get_db") as mock_get_db, \
         patch("app.routers.events.get_user_org", return_value=MOCK_ORG_ID):
        mock_get_db.return_value = MagicMock()
        response = client.post("/api/v1/events/", json=bad_payload)
    assert response.status_code == 422
    assert "date_to" in response.json()["detail"]


def test_create_event_missing_required_field(client):
    """Missing required field should return 422."""
    payload = {**CREATE_PAYLOAD}
    del payload["name"]
    response = client.post("/api/v1/events/", json=payload)
    assert response.status_code == 422


def test_delete_event_not_found(client):
    with patch("app.routers.events.get_db") as mock_get_db, \
         patch("app.routers.events.get_user_org", return_value=MOCK_ORG_ID):
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.maybe_single.return_value.execute.return_value.data = None
        mock_get_db.return_value = db

        response = client.delete("/api/v1/events/nonexistent-id")
        assert response.status_code == 404
