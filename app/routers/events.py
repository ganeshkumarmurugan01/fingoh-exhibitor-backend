from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user, get_user_org
from app.database import get_db
from app.models.event import (
    EventCreate,
    EventUpdate,
    EventResponse,
    EventDetailResponse,
)
from typing import List

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/", response_model=List[EventResponse])
def list_events(current_user: dict = Depends(get_current_user)):
    """
    Returns all events for the current user's organisation,
    ordered by most recently created first.
    """
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)

    result = (
        db.table("events")
        .select("*")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


@router.post("/", response_model=EventDetailResponse, status_code=201)
def create_event(
    payload: EventCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    Creates a new event from the full 5-step wizard payload.
    Inserts the core event row, then related rows for categories,
    ICP config, and exhibitor intent — all in sequence.
    """
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)

    # Validate dates
    if payload.date_to < payload.date_from:
        raise HTTPException(
            status_code=422, detail="date_to must be on or after date_from"
        )

    # 1. Core event row
    event_row = {
        "org_id": org_id,
        "created_by": current_user["user_id"],
        "name": payload.name,
        "type": payload.type,
        "type_label": payload.type_label,
        "date_from": str(payload.date_from),
        "date_to": str(payload.date_to),
        "venue": payload.venue,
        "country": payload.country,
        "company": payload.company,
        "product": payload.product,
        "website": payload.website,
        "booth_size": payload.booth_size,
    }
    event_result = db.table("events").insert(event_row).execute()
    if not event_result.data:
        raise HTTPException(status_code=500, detail="Failed to create event")
    event = event_result.data[0]
    event_id = event["id"]

    # 2. Categories
    if payload.categories:
        cats = [{"event_id": event_id, "category": c} for c in payload.categories]
        db.table("event_categories").insert(cats).execute()

    # 3. ICP config
    db.table("event_icp").insert({
        "event_id": event_id,
        "roles": payload.icp_roles,
        "company_sizes": payload.icp_company_sizes,
        "visit_reasons": payload.icp_visit_reasons,
    }).execute()

    # 4. Exhibitor intent
    db.table("event_intent").insert({
        "event_id": event_id,
        "intent_why": payload.intent_why,
        "intent_buyers": payload.intent_buyers,
        "intent_signals": payload.intent_signals,
        "buyer_signals": payload.buyer_signals,
    }).execute()

    # Return the full detail response
    return _build_event_detail(event, db)


@router.get("/{event_id}", response_model=EventDetailResponse)
def get_event(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Returns the full event configuration — used by the Event Setup
    summary screen and anywhere the full event config is needed.
    """
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    event = _get_event_or_404(event_id, org_id, db)
    return _build_event_detail(event, db)


@router.patch("/{event_id}", response_model=EventDetailResponse)
def update_event(
    event_id: str,
    payload: EventUpdate,
    current_user: dict = Depends(get_current_user),
):
    """
    Partial update of core event fields.
    Used when editing from Event Setup.
    """
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    _get_event_or_404(event_id, org_id, db)  # auth check

    update_data = {
        k: (str(v) if hasattr(v, "isoformat") else v)
        for k, v in payload.dict().items()
        if v is not None
    }
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        db.table("events")
        .update(update_data)
        .eq("id", event_id)
        .execute()
    )
    event = result.data[0]
    return _build_event_detail(event, db)


@router.patch("/{event_id}/icp", response_model=dict)
def update_icp(
    event_id: str,
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """Update ICP config for an existing event."""
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    _get_event_or_404(event_id, org_id, db)

    allowed = {"roles", "company_sizes", "visit_reasons"}
    update_data = {k: v for k, v in payload.items() if k in allowed}

    result = (
        db.table("event_icp")
        .update(update_data)
        .eq("event_id", event_id)
        .execute()
    )
    return result.data[0] if result.data else {}


@router.patch("/{event_id}/intent", response_model=dict)
def update_intent(
    event_id: str,
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """Update exhibitor intent statement for an existing event."""
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    _get_event_or_404(event_id, org_id, db)

    allowed = {"intent_why", "intent_buyers", "intent_signals", "buyer_signals"}
    update_data = {k: v for k, v in payload.items() if k in allowed}

    result = (
        db.table("event_intent")
        .update(update_data)
        .eq("event_id", event_id)
        .execute()
    )
    return result.data[0] if result.data else {}


@router.delete("/{event_id}", status_code=204)
def delete_event(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Soft delete — sets status to 'archived'.
    Hard delete is intentionally not exposed in Phase 1.
    """
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    _get_event_or_404(event_id, org_id, db)

    db.table("events").update({"status": "archived"}).eq("id", event_id).execute()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_event_or_404(event_id: str, org_id: str, db) -> dict:
    """Fetch an event by ID scoped to the org, or raise 404."""
    result = (
        db.table("events")
        .select("*")
        .eq("id", event_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Event not found")
    return result.data


def _build_event_detail(event: dict, db) -> dict:
    """Join categories, ICP, and intent onto an event row."""
    event_id = event["id"]

    cats = (
        db.table("event_categories")
        .select("category")
        .eq("event_id", event_id)
        .execute()
        .data or []
    )
    icp = (
        db.table("event_icp")
        .select("*")
        .eq("event_id", event_id)
        .maybe_single()
        .execute()
        .data or {}
    )
    intent = (
        db.table("event_intent")
        .select("*")
        .eq("event_id", event_id)
        .maybe_single()
        .execute()
        .data or {}
    )

    return {
        **event,
        "categories": [c["category"] for c in cats],
        "icp": icp,
        "intent": intent,
    }
