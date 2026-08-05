"""
Fingoh CRM Integration — Salesforce OAuth + lead push endpoints.
"""
import logging
import os
import json
import base64
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.auth import get_current_user
from app.database import get_db

logger = logging.getLogger("fingoh.salesforce")

router = APIRouter(prefix="/integrations/salesforce", tags=["salesforce"])

SF_CLIENT_ID     = os.getenv("SALESFORCE_CLIENT_ID")
SF_CLIENT_SECRET = os.getenv("SALESFORCE_CLIENT_SECRET")
SF_REDIRECT_URI  = os.getenv(
    "SALESFORCE_REDIRECT_URI",
    "https://api-dev.fingoh.ai/v1/integrations/salesforce/callback"
)
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://fingoh-exhibitor.vercel.app")

SF_AUTH_URL  = os.getenv("SALESFORCE_AUTH_URL",  "https://login.salesforce.com/services/oauth2/authorize")
SF_TOKEN_URL = os.getenv("SALESFORCE_TOKEN_URL", "https://login.salesforce.com/services/oauth2/token")
SF_SCOPES    = "api refresh_token offline_access"


# ── Token refresh ─────────────────────────────────────────────────────────────

async def _get_sf_access_token(refresh_token: str, instance_url: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            SF_TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
                "client_id":     SF_CLIENT_ID,
                "client_secret": SF_CLIENT_SECRET,
            },
        )
    data = r.json()
    if "access_token" not in data:
        raise HTTPException(502, f"Salesforce token refresh failed: {data}")
    return data["access_token"]


# ── Lead payload builder ──────────────────────────────────────────────────────

def _build_sf_lead(contact: dict, event: dict) -> dict:
    """Map a Fingoh contact to a Salesforce Lead record."""
    event_name = event.get("name", "Trade Fair")
    iei_score  = contact.get("onsite_iei_score") or contact.get("iei_score") or 0
    iei_tier   = contact.get("onsite_iei_tier")  or contact.get("iei_tier")  or ""
    reg_prob   = contact.get("reg_prob")
    attend_pct = f"{round(reg_prob * 100)}%" if reg_prob is not None else "—"

    # Build description from all available signals
    notes_parts = [f"=== Fingoh Event Intelligence — {event_name} ===\n"]
    notes_parts.append(f"IEI Score: {round(iei_score, 1)} ({iei_tier} tier)")
    notes_parts.append(f"Attendance Probability: {attend_pct}")

    meeting = contact.get("meeting")
    if meeting:
        notes_parts.append("\n--- Meeting ---")
        notes_parts.append(f"Status: {meeting.get('status', '—')}")
        if meeting.get("proposed_datetime"):
            notes_parts.append(f"Scheduled: {meeting['proposed_datetime']}")
        if meeting.get("topic"):
            notes_parts.append(f"Topic: {meeting['topic']}")
        if meeting.get("staff_completion_notes"):
            notes_parts.append(f"Staff Notes: {meeting['staff_completion_notes']}")
        if meeting.get("ai_analysis"):
            notes_parts.append(f"AI Analysis: {meeting['ai_analysis']}")

    iei_research = contact.get("iei_research")
    if iei_research:
        notes_parts.append("\n--- IEI Research ---")
        if isinstance(iei_research, dict):
            for k, v in iei_research.items():
                if v:
                    notes_parts.append(f"{k}: {v}")
        else:
            notes_parts.append(str(iei_research))

    notes_parts.append(f"\nSource: Fingoh · {event_name}")
    notes_parts.append(f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    description = "\n".join(notes_parts)

    # Split name
    name_parts = (contact.get("name") or "").split(" ", 1)
    first_name = name_parts[0] if name_parts else "Unknown"
    last_name  = name_parts[1] if len(name_parts) > 1 else "Unknown"

    # Map company size to Salesforce NumberOfEmployees
    size_map = {
        "Enterprise (1000+)": 1000,
        "Large (200-999)":    500,
        "Mid-market (50-199)": 100,
        "SMB (1-49)":         25,
    }
    num_employees = size_map.get(contact.get("company_size", ""), None)

    payload = {
        "FirstName":     first_name,
        "LastName":      last_name,
        "Email":         contact.get("email") or "",
        "Phone":         contact.get("phone") or "",
        "Title":         contact.get("designation") or "",
        "Company":       contact.get("company") or "Unknown",
        "Industry":      contact.get("industry") or "",
        "LeadSource":    f"Exhibition — {event_name}",
        "Description":   description,
        # Custom fields — Salesforce orgs need these created as custom fields
        # to store them; they are included here and will be ignored gracefully
        # if the custom fields don't exist yet.
        "Fingoh_IEI_Score__c": round(float(iei_score), 1) if iei_score else None,
        "Fingoh_IEI_Tier__c":  iei_tier or None,
        "Fingoh_Event__c":     event_name,
    }

    if num_employees:
        payload["NumberOfEmployees"] = num_employees

    # Remove None values — Salesforce rejects null custom fields if not configured
    return {k: v for k, v in payload.items() if v is not None}


# ── OAuth endpoints ───────────────────────────────────────────────────────────

@router.get("/auth-url")
async def sf_auth_url(
    org_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    if not SF_CLIENT_ID:
        raise HTTPException(500, "SALESFORCE_CLIENT_ID not configured")

    state = base64.urlsafe_b64encode(
        json.dumps({
            "org_id": org_id,
            "email": current_user["email"],
        }).encode()
    ).decode()

    url = (
        f"{SF_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={SF_CLIENT_ID}"
        f"&redirect_uri={SF_REDIRECT_URI}"
        f"&scope={SF_SCOPES.replace(' ', '%20')}"
        f"&state={state}"
        f"&prompt=consent"
    )
    return {"url": url}


@router.get("/callback")
async def sf_callback(
    code: Optional[str]  = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}?sf_error={error}")
    if not code or not state:
        return RedirectResponse(f"{FRONTEND_URL}?sf_error=missing_params")

    try:
        ctx          = json.loads(base64.urlsafe_b64decode(state).decode())
        org_id = ctx["org_id"]
        user_email   = ctx["email"]
    except Exception:
        return RedirectResponse(f"{FRONTEND_URL}?sf_error=bad_state")

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        r = await client.post(
            SF_TOKEN_URL,
            data={
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  SF_REDIRECT_URI,
                "client_id":     SF_CLIENT_ID,
                "client_secret": SF_CLIENT_SECRET,
            },
        )
    token_data = r.json()
    logger.info("Salesforce token exchange: %s", list(token_data.keys()))

    if "access_token" not in token_data:
        return RedirectResponse(f"{FRONTEND_URL}?sf_error=token_exchange_failed")

    # Store tokens per exhibitor in crm_connections table
    supabase = get_db()
    supabase.table("crm_connections").upsert({
        "org_id":    org_id,
        "user_email":      user_email,
        "provider":        "salesforce",
        "refresh_token":   token_data.get("refresh_token", ""),
        "access_token":    token_data["access_token"],
        "instance_url":    token_data.get("instance_url", ""),
        "status":          "connected",
        "connected_at":    datetime.now(timezone.utc).isoformat(),
    }, on_conflict="org_id,provider").execute()

    return RedirectResponse(
        f"{FRONTEND_URL}?sf_connected=true&org_id={org_id}"
    )


@router.get("/status")
async def sf_status(
    org_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    supabase = get_db()
    result = supabase.table("crm_connections")\
        .select("status,connected_at,last_pushed_at,pushed_count")\
        .eq("org_id", org_id)\
        .eq("provider", "salesforce")\
        .maybe_single()\
        .execute()
    if not result or not result.data:
        return {"connected": False}
    return {"connected": True, **result.data}


@router.delete("/disconnect")
async def sf_disconnect(
    org_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    supabase = get_db()
    supabase.table("crm_connections")\
        .delete()\
        .eq("org_id", org_id)\
        .eq("provider", "salesforce")\
        .execute()
    return {"ok": True}


# ── Push leads ────────────────────────────────────────────────────────────────

@router.post("/push-leads")
async def push_leads_to_salesforce(
    event_id: str    = Query(...),
    tiers: str       = Query("Hot,Warm"),
    org_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Push Hot/Warm leads from a Fingoh event into Salesforce as Leads."""
    supabase = get_db()

    # Get Salesforce connection for this exhibitor
    conn = supabase.table("crm_connections")\
        .select("refresh_token,access_token,instance_url")\
        .eq("org_id", org_id)\
        .eq("provider", "salesforce")\
        .maybe_single()\
        .execute()

    if not conn or not conn.data:
        raise HTTPException(404, "Salesforce not connected for this exhibitor")

    instance_url  = conn.data["instance_url"]
    refresh_token = conn.data["refresh_token"]

    # Refresh access token
    access_token = await _get_sf_access_token(refresh_token, instance_url)

    # Get event details
    event_res = supabase.table("events").select("name,company").eq("id", event_id).maybe_single().execute()
    event = event_res.data if event_res and event_res.data else {}

    # Get contacts
    contacts_res = supabase.table("audience_contacts")\
        .select("*")\
        .eq("event_id", event_id)\
        .execute()
    contacts = contacts_res.data or []

    # Attach meetings
    meetings_res = supabase.table("meeting_requests")\
        .select("*")\
        .eq("event_id", event_id)\
        .execute()
    meeting_by_contact = {}
    for m in (meetings_res.data or []):
        meeting_by_contact.setdefault(m["contact_id"], m)
    for c in contacts:
        c["meeting"] = meeting_by_contact.get(c["id"])

    # Filter by tier
    tier_list = [t.strip() for t in tiers.split(",")]
    leads = [
        c for c in contacts
        if (c.get("onsite_iei_tier") or c.get("iei_tier")) in tier_list
    ]

    if not leads:
        return {"ok": True, "pushed": 0, "message": "No leads matching requested tiers"}

    # Build Salesforce Lead payloads
    sf_leads = [_build_sf_lead(c, event) for c in leads]

    # Push via Salesforce Composite API (batch of up to 200)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }
    pushed = 0
    errors = []

    for i in range(0, len(sf_leads), 200):
        batch = sf_leads[i:i + 200]
        # Use sObject Collections API for batch insert
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{instance_url}/services/data/v59.0/composite/sobjects",
                headers=headers,
                json={
                    "allOrNone": False,
                    "records": [
                        {"attributes": {"type": "Lead"}, **lead}
                        for lead in batch
                    ],
                },
            )
        logger.info("Salesforce push response: %s %s", r.status_code, r.text[:500])

        if r.status_code in (200, 201):
            results = r.json()
            if isinstance(results, list):
                pushed += len([x for x in results if x.get("success")])
                errors += [x.get("errors") for x in results if not x.get("success") and x.get("errors")]
            else:
                errors.append(f"Unexpected response: {r.text[:200]}")
        else:
            errors.append(f"Batch {i // 200 + 1}: {r.status_code} {r.text[:200]}")

    # Update push stats
    supabase.table("crm_connections").update({
        "last_pushed_at": datetime.now(timezone.utc).isoformat(),
        "pushed_count":   pushed,
        "status":         "connected",
    }).eq("org_id", org_id).eq("provider", "salesforce").execute()

    return {
        "ok":     len(errors) == 0,
        "pushed": pushed,
        "errors": errors,
        "total":  len(leads),
    }
