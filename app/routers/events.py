import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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

logger = logging.getLogger("fingoh.events")

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


def _get_extra_events(db, org_id: str) -> int:
    """Sum extra_events add-ons for this org."""
    try:
        res = db.table("org_addons").select("quantity").eq("org_id", org_id).eq("addon_type", "extra_events").execute()
        return sum(r["quantity"] for r in (res.data or []))
    except Exception:
        return 0


@router.get("/plan-info")
def get_plan_info(current_user: dict = Depends(get_current_user)):
    db     = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    org_res = db.table("organisations").select("plan, max_events, status, subscription_expires_at").eq("id", org_id).maybe_single().execute()
    if not org_res or not org_res.data:
        return {"plan": "trial", "max_events": 1, "active_events": 0, "limit_reached": False}
    org           = org_res.data
    plan          = org.get("plan", "trial")
    max_from_plan = PLAN_EVENT_LIMITS.get(plan, 1)
    base_max      = org.get("max_events") or max_from_plan
    extra_events  = _get_extra_events(db, org_id)
    max_events    = base_max + extra_events
    active_count  = db.table("events").select("id", count="exact").eq("org_id", org_id).neq("status", "archived").execute()
    active_events = active_count.count or 0
    return {
        "plan":                    plan,
        "max_events":              max_events,
        "base_max_events":         base_max,
        "extra_events":            extra_events,
        "active_events":           active_events,
        "limit_reached":           active_events >= max_events,
        "status":                  org.get("status", "active"),
        "subscription_expires_at": org.get("subscription_expires_at"),
    }



PLAN_EVENT_LIMITS = {
    # Current plans
    "trial":             1,
    "starter":           1,
    "growth":            1,
    "scale":             1,
    # Legacy plan IDs — backward compat for existing orgs
    "single_event":      1,
    "event_bundle":      6,
    "event_portfolio":   15,
    "annual_self_serve": 999,
    "annual_enterprise": 999,
    "pro":               10,
    "enterprise":        999,
}


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

    # Plan enforcement — check active event count vs limit (including add-ons)
    org_res = db.table("organisations").select("plan, max_events").eq("id", org_id).maybe_single().execute()
    if org_res and org_res.data:
        org_plan      = org_res.data.get("plan", "trial")
        max_from_plan = PLAN_EVENT_LIMITS.get(org_plan, 1)
        base_max      = org_res.data.get("max_events") or max_from_plan
        extra_events  = _get_extra_events(db, org_id)
        max_events    = base_max + extra_events
        active_count  = db.table("events").select("id", count="exact").eq("org_id", org_id).neq("status", "archived").execute()
        current_count = active_count.count or 0
        if current_count >= max_events:
            raise HTTPException(
                status_code=403,
                detail=f"Event limit reached. Your {org_plan} plan allows {max_events} active event(s) (including {extra_events} add-on event(s)). Archive an existing event or upgrade your plan."
            )

    # Determine initial IEI credit allocation from plan
    from app.routers.audience import _get_plan_limits
    plan_limits   = _get_plan_limits(db, org_id)
    initial_credits = plan_limits["max_deep_iei"] * 10

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
        "iei_credits": initial_credits,
    }
    event_result = db.table("events").insert(event_row).execute()
    if not event_result.data:
        raise HTTPException(status_code=500, detail="Failed to create event")
    event = event_result.data[0]
    event_id = event["id"]
    try:
        log_activity(db, org_id, "event_created", f"Created event: {payload.name}", current_user["user_id"], {"event_name": payload.name, "event_id": event_id})
    except Exception as e:
        logger.error("Activity log failed: %s", e)

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


# ── AI-powered event ICP research ────────────────────────────────────────────
import httpx as _httpx
import os as _os


@router.get("/research-icp")
async def research_event_icp(
    event_name: str,
    venue: str = "",
    current_user: dict = Depends(get_current_user),
):
    """
    Use Claude with web search to find real visitor profiles and categories
    for a given event name. Returns suggested visitor segments, roles, and
    categories based on the event's actual audience.
    """
    ANTHROPIC_API_KEY = _os.getenv("ANTHROPIC_API_KEY")
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    prompt = f"""You are an event intelligence analyst. Research the trade fair or exhibition called "{event_name}"{f" at {venue}" if venue else ""}.

Find information about:
1. Who attends this event (visitor profiles, job titles, industries)
2. What categories/sectors are represented
3. What are the primary visit reasons (sourcing, evaluation, research etc.)
4. Typical company sizes of attendees

Based on your research, return ONLY a JSON object (no markdown, no explanation) with this exact structure:
{{
  "found": true,
  "event_description": "One sentence describing the event",
  "visitor_categories": ["Category 1", "Category 2", ...],
  "visitor_roles": ["Role 1", "Role 2", ...],
  "company_sizes": ["Size 1", "Size 2", ...],
  "visit_reasons": ["Reason 1", "Reason 2", ...],
  "industries": ["Industry 1", "Industry 2", ...],
  "source_hint": "Brief note on where this info was found"
}}

Rules:
- visitor_categories: 6-12 specific product/technology categories visitors come to see
- visitor_roles: 6-10 actual job titles common at this event
- company_sizes: 3-5 company size segments that typically attend
- visit_reasons: 4-6 primary reasons visitors attend
- industries: 4-8 industries represented
- If you cannot find specific info, make reasonable inferences based on the event name and type
- Keep all items concise (under 40 chars each)
- Return ONLY the JSON, nothing else"""

    try:
        async with _httpx.AsyncClient(timeout=45) as client:
            res = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":         ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":   "claude-opus-4-8",
                    "max_tokens": 800,
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        if res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Claude error: {res.text[:200]}")

        data = res.json()
        # Extract text from response content blocks
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        # Parse JSON from response
        import json, re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return result
        else:
            # Fallback if Claude didn't return valid JSON
            return {
                "found": False,
                "event_description": f"{event_name} trade fair",
                "visitor_categories": [],
                "visitor_roles": [],
                "company_sizes": [],
                "visit_reasons": [],
                "industries": [],
                "source_hint": "Could not parse response",
            }

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ICP research failed: {str(e)}")


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
    background_tasks: BackgroundTasks,
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
    icp_changed = False
    if icp_fields:
        existing_icp = db.table("event_icp").select("id").eq("event_id", event_id).execute()
        if existing_icp.data:
            db.table("event_icp").update(icp_fields).eq("event_id", event_id).execute()
        else:
            db.table("event_icp").insert({"event_id": event_id, **icp_fields}).execute()
        icp_changed = True

    # Intent — upsert
    intent_fields = {}
    if payload.intent_why is not None: intent_fields["intent_why"] = payload.intent_why
    if payload.intent_buyers is not None: intent_fields["intent_buyers"] = payload.intent_buyers
    if payload.finetune_notes is not None: intent_fields["finetune_notes"] = payload.finetune_notes
    if payload.intent_signals is not None: intent_fields["intent_signals"] = payload.intent_signals
    if payload.buyer_signals is not None: intent_fields["buyer_signals"] = payload.buyer_signals
    if intent_fields:
        existing_intent = db.table("event_intent").select("id").eq("event_id", event_id).execute()
        if existing_intent.data:
            db.table("event_intent").update(intent_fields).eq("event_id", event_id).execute()
        else:
            db.table("event_intent").insert({"event_id": event_id, **intent_fields}).execute()

    if icp_changed:
        from app.routers.audience import rescore_all
        background_tasks.add_task(rescore_all, event_id)
        logger.info("ICP changed for event %s — rescoring contacts in background", event_id)

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
    events_res = db.table("events").select("id,name,date_from,date_to,venue,status").eq("org_id", org_id).neq("status", "archived").execute()
    return events_res.data or []

