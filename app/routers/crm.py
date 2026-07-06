"""
Fingoh CRM Integration — Zoho CRM OAuth + contact sync endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from app.auth import get_current_user, get_user_org
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


def _map_zoho_contact(record: dict, event_id: str, exhibitor_id: str) -> dict:
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
        "exhibitor_id":  exhibitor_id,
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
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    supabase     = get_db()
    exhibitor_id = get_user_org(current_user)

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
        _map_zoho_contact(r, event_id, exhibitor_id)
        for r in zoho_records
        if r.get("Email")
    ]

    synced = 0
    for i in range(0, len(rows), 100):
        supabase.table("audience_contacts").upsert(
            rows[i:i+100],
            on_conflict="event_id,email"
        ).execute()
        synced += len(rows[i:i+100])

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
