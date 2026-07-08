from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user, get_user_org
from app.routers.utils import log_activity
from app.database import get_db
from app.models.event import (
    EventCreate,
    EventUpdate,
    EventResponse,
    EventDetailResponse,
    TargetingUpdate,
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
    try:
        log_activity(db, org_id, "event_created", f"Created event: {payload.name}", current_user["user_id"], {"event_name": payload.name, "event_id": event_id})
    except Exception as e:
        print(f"[events] Activity log failed: {e}")

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


@router.patch("/{event_id}/targeting", response_model=EventDetailResponse)
def update_targeting(
    event_id: str,
    payload: TargetingUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update categories, ICP, and exhibitor intent — upserts into related tables."""
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    event = _get_event_or_404(event_id, org_id, db)

    # Categories — replace entirely if provided
    if payload.categories is not None:
        db.table("event_categories").delete().eq("event_id", event_id).execute()
        if payload.categories:
            cats = [{"event_id": event_id, "category": c} for c in payload.categories]
            db.table("event_categories").insert(cats).execute()

    # ICP — upsert (update if exists, insert if not)
    icp_fields = {}
    if payload.icp_roles is not None: icp_fields["roles"] = payload.icp_roles
    if payload.icp_company_sizes is not None: icp_fields["company_sizes"] = payload.icp_company_sizes
    if payload.icp_visit_reasons is not None: icp_fields["visit_reasons"] = payload.icp_visit_reasons
    if icp_fields:
        existing_icp = db.table("event_icp").select("id").eq("event_id", event_id).execute()
        if existing_icp.data:
            db.table("event_icp").update(icp_fields).eq("event_id", event_id).execute()
        else:
            db.table("event_icp").insert({"event_id": event_id, **icp_fields}).execute()

    # Intent — upsert
    intent_fields = {}
    if payload.intent_why is not None: intent_fields["intent_why"] = payload.intent_why
    if payload.intent_buyers is not None: intent_fields["intent_buyers"] = payload.intent_buyers
    if payload.intent_signals is not None: intent_fields["intent_signals"] = payload.intent_signals
    if payload.buyer_signals is not None: intent_fields["buyer_signals"] = payload.buyer_signals
    if intent_fields:
        existing_intent = db.table("event_intent").select("id").eq("event_id", event_id).execute()
        if existing_intent.data:
            db.table("event_intent").update(intent_fields).eq("event_id", event_id).execute()
        else:
            db.table("event_intent").insert({"event_id": event_id, **intent_fields}).execute()

    return _build_event_detail(event, db)


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


@router.get("/public/by-email/{email}")
async def get_events_for_staff(email: str):
    """
    Public endpoint — returns events for the org that has this staff email.
    Used by Staff App login to show event dropdown.
    """
    db = get_db()

    # Find the staff member's org
    staff_res = db.table("staff").select("org_id").eq("email", email).maybe_single().execute()
    if not staff_res or not staff_res.data:
        raise HTTPException(404, "No events found for this email")

    org_id = staff_res.data["org_id"]

    # Get events for this org
    events_res = db.table("events").select("id,name,date_from,date_to,venue,status").eq("org_id", org_id).execute()
    return events_res.data or []
