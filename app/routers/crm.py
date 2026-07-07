"""
Fingoh CRM Integration — Zoho CRM OAuth + contact sync endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from app.auth import get_current_user
from app.routers.audience import _score_batch, _enrich_visitor, _get_event_context
from app.database import get_db
import os, httpx, json
from typing import Optional
from datetime import datetime, timezone

router = APIRouter(prefix="/crm", tags=["crm"])

ZOHOCRM_CLIENT_ID     = os.getenv("ZOHOCRM_CLIENTID")
ZOHOCRM_CLIENT_SECRET = os.getenv("ZOHOCRM_CLIENT_SECRET")
ZOHOCRM_REDIRECT_URI  = os.getenv(
    "ZOHOCRM_REDIRECT_URI",
    "https://web-production-93e78d.up.railway.app/api/v1/crm/zoho/callback"
)
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://fingoh-exhibitor.vercel.app")

ZOHOCRM_SCOPES = "ZohoCRM.modules.contacts.READ,ZohoCRM.modules.leads.READ"


async def _get_crm_access_token(refresh_token: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://accounts.zoho.com/oauth/v2/token",
            data={
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
                "client_id":     ZOHOCRM_CLIENT_ID,
                "client_secret": ZOHOCRM_CLIENT_SECRET,
            },
        )
    data = r.json()
    if "access_token" not in data:
        raise HTTPException(502, f"Zoho CRM token refresh failed: {data}")
    return data["access_token"]


async def _fetch_zoho_contacts(access_token: str) -> list:
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    contacts = []
    for module in ["Contacts", "Leads"]:
        page = 1
        while True:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"https://www.zohoapis.com/crm/v3/{module}",
                    headers=headers,
                    params={
                        "fields": "First_Name,Last_Name,Email,Phone,Title,Account_Name,Lead_Source,Industry,No_of_Employees,Description",
                        "page":   page,
                        "per_page": 200,
                    },
                )
            if r.status_code != 200:
                break
            data = r.json()
            records = data.get("data", [])
            if not records:
                break
            contacts.extend(records)
            if not data.get("info", {}).get("more_records", False):
                break
            page += 1
    return contacts


def _map_zoho_contact(record: dict, event_id: str) -> dict:
    first = (record.get("First_Name") or "").strip()
    last  = (record.get("Last_Name")  or "").strip()
    name  = f"{first} {last}".strip() or record.get("Email", "Unknown")

    company = ""
    acct = record.get("Account_Name")
    if isinstance(acct, dict):
        company = acct.get("name", "")
    elif isinstance(acct, str):
        company = acct

    employees_raw = record.get("No_of_Employees") or 0
    try:
        employees = int(employees_raw)
    except (ValueError, TypeError):
        employees = 0

    if employees >= 1000:
        company_size = "Enterprise (1000+)"
    elif employees >= 200:
        company_size = "Large (200-999)"
    elif employees >= 50:
        company_size = "Mid-market (50-199)"
    elif employees > 0:
        company_size = "SMB (1-49)"
    else:
        company_size = ""

    return {
        "event_id":      event_id,
        "name":          name,
        "email":         record.get("Email") or "",
        "phone":         record.get("Phone") or "",
        "designation":   record.get("Title") or "",
        "company":       company,
        "company_size":  company_size,
        "industry":      record.get("Industry") or "",
        "source":        record.get("Lead_Source") or "Zoho CRM",
        "notes":         record.get("Description") or "",
        "crm_source":    "zoho",
        "crm_record_id": str(record.get("id", "")),
    }


@router.get("/zoho/auth-url")
async def zoho_auth_url(
    event_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    if not ZOHOCRM_CLIENT_ID:
        raise HTTPException(500, "ZOHOCRM_CLIENTID not configured")
    import base64
    state = base64.urlsafe_b64encode(
        json.dumps({"event_id": event_id, "email": current_user["email"]}).encode()
    ).decode()
    url = (
        "https://accounts.zoho.com/oauth/v2/auth"
        f"?response_type=code"
        f"&client_id={ZOHOCRM_CLIENT_ID}"
        f"&scope={ZOHOCRM_SCOPES}"
        f"&redirect_uri={ZOHOCRM_REDIRECT_URI}"
        f"&access_type=offline"
        f"&state={state}"
        f"&prompt=consent"
    )
    return {"url": url}


@router.get("/zoho/callback")
async def zoho_callback(
    code: Optional[str]  = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}?crm_error={error}")
    if not code or not state:
        return RedirectResponse(f"{FRONTEND_URL}?crm_error=missing_params")

    import base64
    try:
        ctx          = json.loads(base64.urlsafe_b64decode(state).decode())
        event_id     = ctx["event_id"]
        user_email   = ctx["email"]
    except Exception:
        return RedirectResponse(f"{FRONTEND_URL}?crm_error=bad_state")

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://accounts.zoho.com/oauth/v2/token",
            data={
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  ZOHOCRM_REDIRECT_URI,
                "client_id":     ZOHOCRM_CLIENT_ID,
                "client_secret": ZOHOCRM_CLIENT_SECRET,
            },
        )
    token_data = r.json()

    if "refresh_token" not in token_data:
        return RedirectResponse(f"{FRONTEND_URL}?crm_error=token_exchange_failed")

    supabase = get_db()
    supabase.table("crm_connections").upsert({
        "event_id":      event_id,
        "user_email":    user_email,
        "provider":      "zoho",
        "refresh_token": token_data["refresh_token"],
        "status":        "connected",
        "connected_at":  datetime.now(timezone.utc).isoformat(),
    }, on_conflict="event_id,provider").execute()

    return RedirectResponse(
        f"{FRONTEND_URL}?crm_connected=zoho&event_id={event_id}"
    )


@router.post("/zoho/sync")
async def zoho_sync(
    event_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    supabase     = get_db()
    conn = supabase.table("crm_connections")\
        .select("refresh_token")\
        .eq("event_id", event_id)\
        .eq("provider", "zoho")\
        .single()\
        .execute()

    if not conn.data:
        raise HTTPException(404, "Zoho CRM not connected for this event")

    access_token  = await _get_crm_access_token(conn.data["refresh_token"])
    zoho_records  = await _fetch_zoho_contacts(access_token)

    rows = [
        _map_zoho_contact(r, event_id)
        for r in zoho_records
        if r.get("Email")
    ]

    # Enrich contacts with Claude signals before scoring
    import asyncio
    event_ctx = _get_event_context(supabase, event_id)
    enriched_rows = rows
    if True:  # always enrich
        async with httpx.AsyncClient() as http_client:
            sem = asyncio.Semaphore(3)
            async def enrich_one(row):
                async with sem:
                    signals = await _enrich_visitor(row, event_ctx, http_client)
                    return {**row, **signals}
            enriched_rows = await asyncio.gather(*[enrich_one(r) for r in rows])

    # Score contacts via XGBoost
    scored = await _score_batch(enriched_rows)

    # Merge scores into rows
    for row, score in zip(enriched_rows, scored):
        row["iei_score"] = score["ieiScore"]
        row["reg_prob"]  = score["regProb"]
        from datetime import datetime, timezone; row["scored_at"] = datetime.now(timezone.utc).isoformat()

    # Keep only known DB columns before upsert
    ALLOWED_COLS = {
        "event_id","name","email","company","designation","phone",
        "city","country","source","notes","industry","company_size",
        "crm_source","crm_record_id","iei_score","reg_prob","scored_at",
        "meeting_interest","iei_tier","raw_data",
    }
    clean_rows = [{k: v for k, v in r.items() if k in ALLOWED_COLS} for r in enriched_rows]

    synced = 0
    for i in range(0, len(clean_rows), 100):
        supabase.table("audience_contacts").upsert(
            clean_rows[i:i+100],
            on_conflict="event_id,email"
        ).execute()
        synced += len(clean_rows[i:i+100])

    supabase.table("crm_connections").update({
        "status":         "synced",
        "record_count":   synced,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }).eq("event_id", event_id).eq("provider", "zoho").execute()

    return {"ok": True, "synced": synced}


@router.get("/status")
async def crm_status(
    event_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    supabase = get_db()
    result = supabase.table("crm_connections")\
        .select("provider,status,record_count,last_synced_at,connected_at")\
        .eq("event_id", event_id)\
        .execute()
    return {"connections": result.data or []}


@router.delete("/zoho/disconnect")
async def zoho_disconnect(
    event_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    supabase = get_db()
    supabase.table("crm_connections")\
        .delete()\
        .eq("event_id", event_id)\
        .eq("provider", "zoho")\
        .execute()
    return {"ok": True}


# ── Helper: build Zoho CRM Lead payload from Fingoh contact ──────────────────

def _build_zoho_lead(contact: dict, event: dict) -> dict:
    """Map a Fingoh contact + event to a Zoho CRM Lead record."""
    event_name = event.get("name", "Trade Fair")
    iei_score  = contact.get("onsite_iei_score") or contact.get("iei_score") or 0
    iei_tier   = contact.get("onsite_iei_tier")  or contact.get("iei_tier")  or ""
    reg_prob   = contact.get("reg_prob")
    attend_pct = f"{round(reg_prob*100)}%" if reg_prob is not None else "—"

    # Build comprehensive notes from all available signals
    notes_parts = [f"=== Fingoh Event Intelligence — {event_name} ===\n"]

    notes_parts.append(f"IEI Score: {round(iei_score, 1)} ({iei_tier} tier)")
    notes_parts.append(f"Attendance Probability: {attend_pct}")

    meeting = contact.get("meeting")
    if meeting:
        notes_parts.append(f"\n--- Meeting ---")
        notes_parts.append(f"Status: {meeting.get('status','—')}")
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
        notes_parts.append(f"\n--- IEI Research ---")
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
    first_name = name_parts[0] if name_parts else ""
    last_name  = name_parts[1] if len(name_parts) > 1 else ""

    return {
        "First_Name":   first_name,
        "Last_Name":    last_name or first_name or "Unknown",
        "Email":        contact.get("email") or "",
        "Phone":        contact.get("phone") or "",
        "Title":        contact.get("designation") or "",
        "Company":      contact.get("company") or "",
        "Industry":     contact.get("industry") or "",
        "Lead_Source":  f"Exhibition — {event_name}",
        "Description":  description,
        "Tag":          [{"name": "fingoh-lead"}, {"name": event_name}],
    }


# ── Push leads to Zoho CRM ────────────────────────────────────────────────────

@router.post("/zoho/push-leads")
async def push_leads_to_zoho(
    event_id: str = Query(...),
    tiers: str = Query("Hot,Warm"),  # comma-separated tiers to push
    current_user: dict = Depends(get_current_user),
):
    """Push Hot/Warm leads from a Fingoh event into Zoho CRM as Leads."""
    supabase = get_db()

    # Get CRM connection
    conn = supabase.table("crm_connections")\
        .select("refresh_token")\
        .eq("event_id", event_id)\
        .eq("provider", "zoho")\
        .single()\
        .execute()

    if not conn.data:
        raise HTTPException(404, "Zoho CRM not connected for this event")

    access_token = await _get_crm_access_token(conn.data["refresh_token"])

    # Get event details
    event_res = supabase.table("events").select("name,company").eq("id", event_id).maybe_single().execute()
    event = event_res.data if event_res and event_res.data else {}

    # Get contacts for this event with meeting + research data
    contacts_res = supabase.table("audience_contacts")\
        .select("*")\
        .eq("event_id", event_id)\
        .execute()
    contacts = contacts_res.data or []

    # Get meetings for this event
    meetings_res = supabase.table("meeting_requests")\
        .select("*")\
        .eq("event_id", event_id)\
        .execute()
    meeting_by_contact = {}
    for m in (meetings_res.data or []):
        meeting_by_contact.setdefault(m["contact_id"], m)

    for c in contacts:
        c["meeting"] = meeting_by_contact.get(c["id"])

    # Filter by requested tiers
    tier_list = [t.strip() for t in tiers.split(",")]
    leads = [
        c for c in contacts
        if (c.get("onsite_iei_tier") or c.get("iei_tier")) in tier_list
    ]

    if not leads:
        return {"ok": True, "pushed": 0, "message": "No leads matching requested tiers"}

    # Build Zoho Lead payloads
    zoho_leads = [_build_zoho_lead(c, event) for c in leads]

    # Push to Zoho CRM in batches of 100
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    pushed = 0
    errors = []

    for i in range(0, len(zoho_leads), 100):
        batch = zoho_leads[i:i+100]
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://www.zohoapis.com/crm/v3/Leads",
                headers=headers,
                json={"data": batch, "trigger": ["workflow"]},
            )
        if r.status_code in (200, 201):
            result = r.json()
            pushed += len([d for d in result.get("data", []) if d.get("code") in ("SUCCESS", "DUPLICATE_DATA")])
        else:
            errors.append(f"Batch {i//100+1}: {r.status_code} {r.text[:200]}")

    return {
        "ok":     len(errors) == 0,
        "pushed": pushed,
        "errors": errors,
        "total":  len(leads),
    }
