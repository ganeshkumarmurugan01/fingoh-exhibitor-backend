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


@router.get("", response_model=List[EventResponse])
def list_events(current_user: dict = Depends(get_current_user)):
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)

    result = (
        db.table("events")
        .select("*")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data if result.data else []


@router.post("", response_model=EventDetailResponse, status_code=201)
def create_event(
    payload: EventCreate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)

    if payload.date_to < payload.date_from:
        raise HTTPException(
            status_code=422, detail="date_to must be on or after date_from"
        )

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

    if payload.categories:
        cats = [{"event_id": event_id, "category": c} for c in payload.categories]
        db.table("event_categories").insert(cats).execute()

    db.table("event_icp").insert({
        "event_id": event_id,
        "roles": payload.icp_roles,
        "company_sizes": payload.icp_company_sizes,
        "visit_reasons": payload.icp_visit_reasons,
    }).execute()

    db.table("event_intent").insert({
        "event_id": event_id,
        "intent_why": payload.intent_why,
        "intent_buyers": payload.intent_buyers,
        "intent_signals": payload.intent_signals,
        "buyer_signals": payload.buyer_signals,
    }).execute()

    return _build_event_detail(event, db)


@router.get("/{event_id}", response_model=EventDetailResponse)
def get_event(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
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
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    _get_event_or_404(event_id, org_id, db)

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
    return _build_event_detail(result.data[0], db)


@router.delete("/{event_id}", status_code=204)
def delete_event(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    _get_event_or_404(event_id, org_id, db)
    db.table("events").update({"status": "archived"}).eq("id", event_id).execute()


def _get_event_or_404(event_id: str, org_id: str, db) -> dict:
    result = (
        db.table("events")
        .select("*")
        .eq("id", event_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Event not found")
    return result.data[0]


def _build_event_detail(event: dict, db) -> dict:
    event_id = event["id"]

    cats = db.table("event_categories").select("category").eq("event_id", event_id).execute()
    icp = db.table("event_icp").select("*").eq("event_id", event_id).execute()
    intent = db.table("event_intent").select("*").eq("event_id", event_id).execute()

    return {
        **event,
        "categories": [c["category"] for c in (cats.data or [])],
        "icp": icp.data[0] if icp.data else {},
        "intent": intent.data[0] if intent.data else {},
    }
