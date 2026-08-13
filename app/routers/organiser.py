# routers/organiser.py
# Fingoh Organiser Module — Phase 1: Auth + Account management

import os
import uuid
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import jwt
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client

router = APIRouter(tags=["organiser"])

# ── Supabase client ──────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
JWT_SECRET   = os.environ.get("JWT_SECRET", "fingoh-secret-change-in-prod")
JWT_ALGO     = "HS256"
JWT_EXPIRY_H = 24

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── JWT helpers ──────────────────────────────────────────────────────────────
def create_organiser_token(user_id: str, organiser_id: str, role: str) -> str:
    payload = {
        "sub":          user_id,
        "organiser_id": organiser_id,
        "role":         role,
        "type":         "organiser",
        "exp":          datetime.utcnow() + timedelta(hours=JWT_EXPIRY_H),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_organiser_token(token: str) -> dict:
    try:
        if token and token.startswith("Bearer "):
            token = token[7:]
        token = token.strip()
        from jose import jwt as jose_jwt
        from jose.exceptions import ExpiredSignatureError
        payload = jose_jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        if payload.get("type") != "organiser":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


def get_current_organiser_user(x_fingoh_auth: Optional[str] = Header(None)) -> dict:
    if not x_fingoh_auth:
        raise HTTPException(status_code=401, detail="Missing auth header")
    return decode_organiser_token(x_fingoh_auth)


# ── Pydantic models ──────────────────────────────────────────────────────────
class OrganiserLoginRequest(BaseModel):
    email: str
    password: str


class CreateOrganiserRequest(BaseModel):
    name: str
    contact_email: str
    logo_url: Optional[str] = None
    exhibitor_quota: int = 10
    data_quota: int = 1000
    admin_email: str        # first organiser_user email
    admin_password: str
    admin_full_name: str


class CreateOrganiserEventRequest(BaseModel):
    name: str
    venue: Optional[str] = None
    start_date: Optional[str] = None   # ISO date string
    end_date: Optional[str] = None
    industry_vertical: Optional[str] = None


class UpdateOrganiserEventRequest(BaseModel):
    name: Optional[str] = None
    venue: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    industry_vertical: Optional[str] = None
    status: Optional[str] = None


# ── Auth routes ──────────────────────────────────────────────────────────────
@router.post("/organiser/login")
def organiser_login(body: OrganiserLoginRequest):
    sb = get_supabase()

    # fetch user by email
    result = sb.table("organiser_users").select(
        "id, organiser_id, email, password_hash, full_name, role"
    ).eq("email", body.email.lower().strip()).maybe_single().execute()

    if not result or not result.data:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = result.data

    # verify password
    if not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # fetch organiser details
    org_result = sb.table("organisers").select(
        "id, name, logo_url, status, exhibitor_quota, data_quota, exhibitor_used, data_used"
    ).eq("id", user["organiser_id"]).maybe_single().execute()

    if not org_result or not org_result.data:
        raise HTTPException(status_code=404, detail="Organiser account not found")

    organiser = org_result.data

    if organiser["status"] != "active":
        raise HTTPException(status_code=403, detail="Organiser account is not active")

    # update last_login
    sb.table("organiser_users").update(
        {"last_login": datetime.utcnow().isoformat()}
    ).eq("id", user["id"]).execute()

    token = create_organiser_token(user["id"], user["organiser_id"], user["role"])

    return {
        "token":     token,
        "user": {
            "id":        user["id"],
            "email":     user["email"],
            "full_name": user["full_name"],
            "role":      user["role"],
        },
        "organiser": organiser,
    }


@router.get("/organiser/me")
def organiser_me(current_user: dict = Depends(get_current_organiser_user)):
    sb = get_supabase()

    user_result = sb.table("organiser_users").select(
        "id, email, full_name, role, last_login, organiser_id"
    ).eq("id", current_user["sub"]).maybe_single().execute()

    if not user_result or not user_result.data:
        raise HTTPException(status_code=404, detail="User not found")

    org_result = sb.table("organisers").select("*").eq(
        "id", current_user["organiser_id"]
    ).maybe_single().execute()

    if not org_result or not org_result.data:
        raise HTTPException(status_code=404, detail="Organiser not found")

    return {
        "user":      user_result.data,
        "organiser": org_result.data,
    }


# ── Organiser Event routes ───────────────────────────────────────────────────
@router.post("/organiser/events")
def create_organiser_event(
    body: CreateOrganiserEventRequest,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    payload = {
        "id":                  str(uuid.uuid4()),
        "organiser_id":        organiser_id,
        "name":                body.name,
        "venue":               body.venue,
        "start_date":          body.start_date,
        "end_date":            body.end_date,
        "industry_vertical":   body.industry_vertical,
        "status":              "draft",
    }

    result = sb.table("organiser_events").insert(payload).execute()

    if not result or not result.data:
        raise HTTPException(status_code=500, detail="Failed to create event")

    return result.data[0]


@router.get("/organiser/events")
def list_organiser_events(current_user: dict = Depends(get_current_organiser_user)):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    result = sb.table("organiser_events").select("*").eq(
        "organiser_id", organiser_id
    ).order("created_at", desc=True).execute()

    return result.data or []


@router.get("/organiser/events/{event_id}")
def get_organiser_event(
    event_id: str,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    result = sb.table("organiser_events").select("*").eq(
        "id", event_id
    ).eq("organiser_id", organiser_id).maybe_single().execute()

    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Event not found")

    return result.data


@router.patch("/organiser/events/{event_id}")
def update_organiser_event(
    event_id: str,
    body: UpdateOrganiserEventRequest,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # verify ownership
    check = sb.table("organiser_events").select("id").eq(
        "id", event_id
    ).eq("organiser_id", organiser_id).maybe_single().execute()

    if not check or not check.data:
        raise HTTPException(status_code=404, detail="Event not found")

    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = sb.table("organiser_events").update(updates).eq("id", event_id).execute()

    return result.data[0] if result.data else {"status": "updated"}


# ── Admin-only: create organiser account ─────────────────────────────────────
# Called from fingoh-admin backend — protected by admin JWT (handled in admin.py)
# Exposed here as an internal utility endpoint
@router.post("/organiser/admin/create-organiser")
def admin_create_organiser(
    body: CreateOrganiserRequest,
    x_fingoh_admin_key: Optional[str] = Header(None),
):
    """
    Called by fingoh-admin to provision a new organiser account.
    Protected by a shared admin key (set FINGOH_ADMIN_INTERNAL_KEY in Railway env).
    """
    admin_key = os.environ.get("FINGOH_ADMIN_INTERNAL_KEY", "")
    if not admin_key or x_fingoh_admin_key != admin_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    sb = get_supabase()

    # check email not already used
    existing = sb.table("organiser_users").select("id").eq(
        "email", body.admin_email.lower().strip()
    ).maybe_single().execute()

    if existing and existing.data:
        raise HTTPException(status_code=409, detail="Email already registered")

    # create organiser record
    organiser_id = str(uuid.uuid4())
    org_payload = {
        "id":               organiser_id,
        "name":             body.name,
        "contact_email":    body.contact_email.lower().strip(),
        "logo_url":         body.logo_url,
        "exhibitor_quota":  body.exhibitor_quota,
        "data_quota":       body.data_quota,
        "status":           "active",
    }

    org_result = sb.table("organisers").insert(org_payload).execute()
    if not org_result or not org_result.data:
        raise HTTPException(status_code=500, detail="Failed to create organiser")

    # hash password
    pw_hash = bcrypt.hashpw(
        body.admin_password.encode(), bcrypt.gensalt()
    ).decode()

    # create organiser_user (admin role)
    user_payload = {
        "id":            str(uuid.uuid4()),
        "organiser_id":  organiser_id,
        "email":         body.admin_email.lower().strip(),
        "password_hash": pw_hash,
        "full_name":     body.admin_full_name,
        "role":          "admin",
    }

    user_result = sb.table("organiser_users").insert(user_payload).execute()
    if not user_result or not user_result.data:
        # rollback organiser
        sb.table("organisers").delete().eq("id", organiser_id).execute()
        raise HTTPException(status_code=500, detail="Failed to create organiser user")

    return {
        "organiser_id": organiser_id,
        "message":      "Organiser account created successfully",
        "login_email":  body.admin_email,
    }

    # ── Phase 2: Invite, Visitor Upload, Dashboard ────────────────────────────────
import csv
import io
import httpx
from fastapi import UploadFile, File, Form

# ── Pydantic models ──────────────────────────────────────────────────────────

class InviteExhibitorRequest(BaseModel):
    invite_email: str
    data_allocation: int = 500

class UpdateAllocationRequest(BaseModel):
    data_allocation: int


# ── Email helper ─────────────────────────────────────────────────────────────
async def send_organiser_invite_email(
    invite_email: str,
    organiser_name: str,
    organiser_contact_email: str,
    event_name: str,
    invite_token: str,
    is_existing: bool,
):
    """Send invite email via the platform email system."""
    sb = get_supabase()

    # fetch platform email config - use defaults if table not found
    try:
        cfg_result = sb.table("email_config").select("*").limit(1).execute()
        cfg = cfg_result.data[0] if cfg_result and cfg_result.data else {}
    except Exception:
        cfg = {}

    sender_name   = f"Fingoh (on behalf of {organiser_name})"
    reply_to       = cfg.get("reply_to", "hello@fingoh.ai")
    primary_color  = cfg.get("primary_color", "#0D1B3E")
    footer_text   = f"Sent by Fingoh on behalf of {organiser_name} · Intent Intelligence for B2B Trade Fairs"
    logo_url       = cfg.get("logo_url", "")

    # build accept URL
    base_url = os.environ.get("FRONTEND_URL", "https://exhibitor.fingoh.ai")
    accept_url = f"{base_url}/organiser-invite?token={invite_token}"

    action_text = "Connect your existing Fingoh account" if is_existing else "Create your account and join"

    logo_html = f'<img src="{logo_url}" style="max-height:44px;display:block;" alt="{sender_name}">' if logo_url else f'<span style="color:#fff;font-size:20px;font-weight:800;">{sender_name}</span>'

    html_body = f"""
    <p>Hi there,</p>
    <p><strong>{organiser_name}</strong> has invited you to join their event <strong>{event_name}</strong> on Fingoh — the exhibitor intelligence platform for B2B trade fairs.</p>
    <p>As an exhibitor at this event, you'll get access to:</p>
    <ul>
      <li>Visitor data uploaded by the organiser</li>
      <li>IEI scoring to identify your highest-intent visitors</li>
      <li>Meeting management and outcome tracking</li>
    </ul>
    <p style="margin-top:24px;">
      <a href="{accept_url}" style="background:{primary_color};color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;display:inline-block;">
        {action_text} →
      </a>
    </p>
    <p style="font-size:12px;color:#94A3B8;margin-top:16px;">
      Or copy this link: {accept_url}
    </p>
    <p style="font-size:12px;color:#94A3B8;">This invite link expires in 7 days.</p>
    """

    full_html = f"""<!DOCTYPE html><html><body style="margin:0;padding:16px;background:#F8FAFC;font-family:-apple-system,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table width="540" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;border:1px solid #E2E8F0;overflow:hidden;max-width:540px;">
<tr><td style="background:{primary_color};padding:20px 28px;">{logo_html}</td></tr>
<tr><td style="padding:28px;color:#0F172A;font-size:14px;line-height:1.7;">{html_body}</td></tr>
<tr><td style="background:#F8FAFC;padding:12px 28px;border-top:1px solid #E2E8F0;text-align:center;">
<p style="margin:0;font-size:11px;color:#94A3B8;">{footer_text}</p>
</td></tr></table></td></tr></table></body></html>"""

    # send via Zoho OAuth API (same as meetings/onboarding)
    print(f"[invite email] starting Zoho send to {invite_email}")
    try:
        from app.routers.meetings import get_zoho_access_token
        ZOHO_ACCOUNT_ID = os.getenv("ZOHO_ACCOUNT_ID") or "670863000000008002"
        ZOHO_FROM_EMAIL = os.getenv("ZOHO_FROM_EMAIL", "noreply@fingoh.ai")

        access_token = await get_zoho_access_token()

        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
                headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
                json={
                    "fromAddress": ZOHO_FROM_EMAIL,
                    "toAddress":   invite_email,
                    "subject":     f"{organiser_name} has invited you to join {event_name} on Fingoh",
                    "mailFormat":  "html",
                    "content":     full_html,
                },
            )
        print(f"[invite email] Zoho API status: {resp.status_code} → {invite_email}")
        print(f"[invite email] Zoho response body: {resp.text}")
    except Exception as e:
        print(f"[invite email] Failed: {e}")


# ── Organiser Dashboard ───────────────────────────────────────────────────────
@router.get("/organiser/dashboard")
def organiser_dashboard(current_user: dict = Depends(get_current_organiser_user)):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # fetch organiser quota info
    org = sb.table("organisers").select(
        "id, name, exhibitor_quota, data_quota, exhibitor_used, data_used"
    ).eq("id", organiser_id).maybe_single().execute()

    if not org or not org.data:
        raise HTTPException(status_code=404, detail="Organiser not found")

    # fetch all events
    events = sb.table("organiser_events").select("*").eq(
        "organiser_id", organiser_id
    ).order("created_at", desc=True).execute()

    # fetch all links for aggregate stats
    links = sb.table("organiser_exhibitor_links").select(
        "id, status, data_allocation, data_consumed, organiser_event_id"
    ).eq("organiser_id", organiser_id).execute()

    links_data = links.data or []
    total_invited  = len(links_data)
    total_accepted = len([l for l in links_data if l["status"] in ("accepted", "active")])
    total_data_consumed = sum(l["data_consumed"] for l in links_data)

    return {
        "organiser":  org.data,
        "events":     events.data or [],
        "stats": {
            "total_invited":       total_invited,
            "total_accepted":      total_accepted,
            "total_data_consumed": total_data_consumed,
        }
    }


# ── Exhibitor Management ──────────────────────────────────────────────────────
@router.post("/organiser/events/{event_id}/invite")
async def invite_exhibitor(
    event_id: str,
    body: InviteExhibitorRequest,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # verify event belongs to organiser
    event = sb.table("organiser_events").select(
        "id, name, organiser_id"
    ).eq("id", event_id).eq("organiser_id", organiser_id).maybe_single().execute()

    if not event or not event.data:
        raise HTTPException(status_code=404, detail="Event not found")

    # check exhibitor quota
    org = sb.table("organisers").select(
        "name, exhibitor_quota, exhibitor_used"
    ).eq("id", organiser_id).maybe_single().execute()

    if not org or not org.data:
        raise HTTPException(status_code=404, detail="Organiser not found")

    if org.data["exhibitor_used"] >= org.data["exhibitor_quota"]:
        raise HTTPException(status_code=400, detail="Exhibitor quota reached")

    # check if already invited
    existing = sb.table("organiser_exhibitor_links").select("id, status").eq(
        "organiser_event_id", event_id
    ).eq("invite_email", body.invite_email.lower().strip()).eq("status", "invited").limit(1).execute()

    if existing and existing.data and len(existing.data) > 0:
        raise HTTPException(status_code=409, detail="Exhibitor already invited to this event")

    # check if exhibitor already has a Fingoh account
    existing_org = sb.table("profiles").select(
        "org_id"
    ).eq("email", body.invite_email.lower().strip()).maybe_single().execute()
    exhibitor_id = existing_org.data["org_id"] if existing_org and existing_org.data else None
    is_existing = bool(exhibitor_id)


    # generate invite token
    invite_token = secrets.token_urlsafe(32)

    # create link record
    link_payload = {
        "id":                 str(uuid.uuid4()),
        "organiser_id":       organiser_id,
        "organiser_event_id": event_id,
        "exhibitor_id":       exhibitor_id,
        "invite_email":       body.invite_email.lower().strip(),
        "data_allocation":    body.data_allocation,
        "data_consumed":      0,
        "status":             "invited",
        "invite_token":       invite_token,
        "invited_at":         datetime.utcnow().isoformat(),
    }

    link_result = sb.table("organiser_exhibitor_links").insert(link_payload).execute()
    if not link_result or not link_result.data:
        raise HTTPException(status_code=500, detail="Failed to create invite")

    # increment exhibitor_used
    sb.table("organisers").update(
        {"exhibitor_used": org.data["exhibitor_used"] + 1}
    ).eq("id", organiser_id).execute()

    # send invite email - fire and forget, don't fail invite if email fails
    try:
        await send_organiser_invite_email(
            invite_email=body.invite_email.lower().strip(),
            organiser_name=org.data["name"],
            organiser_contact_email=org.data.get("contact_email", "hello@fingoh.ai"),
            event_name=event.data["name"],
            invite_token=invite_token,
            is_existing=is_existing,
        )
    except Exception as e:
        print(f"[invite] Email failed but invite created: {e}")

    return {
        "message":      "Invite sent successfully",
        "invite_token": invite_token,
        "is_existing":  is_existing,
        "link_id":      link_result.data[0]["id"],
    }


@router.get("/organiser/events/{event_id}/exhibitors")
def list_event_exhibitors(
    event_id: str,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # verify ownership
    event = sb.table("organiser_events").select("id").eq(
        "id", event_id
    ).eq("organiser_id", organiser_id).maybe_single().execute()

    if not event or not event.data:
        raise HTTPException(status_code=404, detail="Event not found")

    links = sb.table("organiser_exhibitor_links").select("*").eq(
        "organiser_event_id", event_id
    ).neq("status", "removed").order("invited_at", desc=True).execute()

    return links.data or []


@router.patch("/organiser/exhibitors/{link_id}/allocate")
def update_allocation(
    link_id: str,
    body: UpdateAllocationRequest,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # verify ownership
    link = sb.table("organiser_exhibitor_links").select(
        "id, data_consumed"
    ).eq("id", link_id).eq("organiser_id", organiser_id).maybe_single().execute()

    if not link or not link.data:
        raise HTTPException(status_code=404, detail="Link not found")

    if body.data_allocation < link.data["data_consumed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Allocation cannot be less than already consumed ({link.data['data_consumed']} rows)"
        )

    sb.table("organiser_exhibitor_links").update(
        {"data_allocation": body.data_allocation}
    ).eq("id", link_id).execute()

    return {"message": "Allocation updated", "data_allocation": body.data_allocation}


@router.delete("/organiser/exhibitors/{link_id}")
def remove_exhibitor(
    link_id: str,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    link = sb.table("organiser_exhibitor_links").select(
        "id, status"
    ).eq("id", link_id).eq("organiser_id", organiser_id).maybe_single().execute()

    if not link or not link.data:
        raise HTTPException(status_code=404, detail="Link not found")

    sb.table("organiser_exhibitor_links").delete().eq("id", link_id).execute()

    # decrement exhibitor_used
    org = sb.table("organisers").select("exhibitor_used").eq(
        "id", organiser_id
    ).maybe_single().execute()

    if org and org.data and org.data["exhibitor_used"] > 0:
        sb.table("organisers").update(
            {"exhibitor_used": org.data["exhibitor_used"] - 1}
        ).eq("id", organiser_id).execute()

    return {"message": "Exhibitor removed"}


# ── Visitor Upload ────────────────────────────────────────────────────────────
@router.post("/organiser/events/{event_id}/visitor-upload")
async def upload_visitor_data(
    event_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # verify event ownership
    event = sb.table("organiser_events").select("id").eq(
        "id", event_id
    ).eq("organiser_id", organiser_id).maybe_single().execute()

    if not event or not event.data:
        raise HTTPException(status_code=404, detail="Event not found")

    # check data quota
    org = sb.table("organisers").select(
        "data_quota, data_used"
    ).eq("id", organiser_id).maybe_single().execute()

    if not org or not org.data:
        raise HTTPException(status_code=404, detail="Organiser not found")

    # read CSV
    content = await file.read()
    decoded = None
    for encoding in ["utf-8-sig", "utf-8", "latin-1", "cp1252"]:
        try:
            decoded = content.decode(encoding)
            break
        except Exception:
            continue
    if decoded is None:
        raise HTTPException(status_code=400, detail="Could not decode CSV file. Please save as UTF-8.")
    try:
        reader = csv.DictReader(io.StringIO(decoded))
        rows   = [row for row in reader]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid CSV: {str(e)}")

    if not rows:
        raise HTTPException(status_code=400, detail="CSV is empty")

    row_count = len(rows)
    remaining_quota = org.data["data_quota"] - org.data["data_used"]

    if row_count > remaining_quota:
        raise HTTPException(
            status_code=400,
            detail=f"Upload exceeds data quota. You have {remaining_quota} rows remaining."
        )

    # create upload record
    upload_id = str(uuid.uuid4())
    upload_payload = {
        "id":                 upload_id,
        "organiser_id":       organiser_id,
        "organiser_event_id": event_id,
        "filename":           file.filename,
        "row_count":          row_count,
    }

    upload_result = sb.table("organiser_visitor_uploads").insert(upload_payload).execute()
    if not upload_result or not upload_result.data:
        raise HTTPException(status_code=500, detail="Failed to create upload record")

    # insert visitor rows in batches of 500
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        row_records = [
            {
                "id":                 str(uuid.uuid4()),
                "upload_id":          upload_id,
                "organiser_event_id": event_id,
                "raw_data":           row,
            }
            for row in batch
        ]
        sb.table("organiser_visitor_rows").insert(row_records).execute()

    # update data_used
    sb.table("organisers").update(
        {"data_used": org.data["data_used"] + row_count}
    ).eq("id", organiser_id).execute()

    return {
        "message":   f"Successfully uploaded {row_count} visitors",
        "upload_id": upload_id,
        "row_count": row_count,
        "remaining_quota": remaining_quota - row_count,
    }


@router.get("/organiser/events/{event_id}/visitor-uploads")
def list_visitor_uploads(
    event_id: str,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    event = sb.table("organiser_events").select("id").eq(
        "id", event_id
    ).eq("organiser_id", organiser_id).maybe_single().execute()

    if not event or not event.data:
        raise HTTPException(status_code=404, detail="Event not found")

    uploads = sb.table("organiser_visitor_uploads").select("*").eq(
        "organiser_event_id", event_id
    ).order("uploaded_at", desc=True).execute()

    return uploads.data or []


@router.delete("/organiser/visitor-uploads/{upload_id}")
def delete_visitor_upload(
    upload_id: str,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    upload = sb.table("organiser_visitor_uploads").select(
        "id, row_count, organiser_id"
    ).eq("id", upload_id).eq("organiser_id", organiser_id).maybe_single().execute()

    if not upload or not upload.data:
        raise HTTPException(status_code=404, detail="Upload not found")

    row_count = upload.data["row_count"]

    # delete rows first
    sb.table("organiser_visitor_rows").delete().eq("upload_id", upload_id).execute()

    # delete upload record
    sb.table("organiser_visitor_uploads").delete().eq("id", upload_id).execute()

    # decrement data_used
    org = sb.table("organisers").select("data_used").eq(
        "id", organiser_id
    ).maybe_single().execute()

    if org and org.data:
        new_used = max(0, org.data["data_used"] - row_count)
        sb.table("organisers").update({"data_used": new_used}).eq("id", organiser_id).execute()

    return {"message": f"Upload deleted, {row_count} rows removed"}


# ── Exhibitor-side: validate and accept invite ────────────────────────────────
@router.get("/organiser/invite/validate/{token}")
def validate_invite_token(token: str):
    sb = get_supabase()

    link = sb.table("organiser_exhibitor_links").select(
        "id, status, invite_email, data_allocation, organiser_event_id, organiser_id, invited_at"
    ).eq("invite_token", token).maybe_single().execute()

    if not link or not link.data:
        raise HTTPException(status_code=404, detail="Invalid or expired invite token")

    if link.data["status"] == "removed":
        raise HTTPException(status_code=400, detail="This invite has been revoked")

    if link.data["status"] in ("accepted", "active"):
        raise HTTPException(status_code=400, detail="This invite has already been accepted")

    # fetch event and organiser details
    event = sb.table("organiser_events").select(
        "id, name, venue, start_date, end_date, industry_vertical"
    ).eq("id", link.data["organiser_event_id"]).maybe_single().execute()

    organiser = sb.table("organisers").select(
        "id, name, logo_url"
    ).eq("id", link.data["organiser_id"]).maybe_single().execute()

    return {
        "link":      link.data,
        "event":     event.data if event else None,
        "organiser": organiser.data if organiser else None,
    }


@router.post("/organiser/invite/accept/{token}")
async def accept_invite(token: str, request: Request):
    """
    Called by exhibitor app after login/registration.
    Links the exhibitor's org to the organiser event.
    """
    from app.auth import get_current_user, get_user_org
    sb = get_supabase()

    try:
        user = await get_current_user(request)
        org_id = get_user_org(user["user_id"], sb)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    # validate token
    link = sb.table("organiser_exhibitor_links").select("*").eq(
        "invite_token", token
    ).maybe_single().execute()

    if not link or not link.data:
        raise HTTPException(status_code=404, detail="Invalid invite token")

    if link.data["status"] in ("accepted", "active", "removed"):
        raise HTTPException(status_code=400, detail="Invite already used or revoked")

    event_id     = link.data["organiser_event_id"]
    organiser_id = link.data["organiser_id"]

    # fetch organiser and event details for the powered label
    organiser = sb.table("organisers").select("name").eq(
        "id", organiser_id
    ).maybe_single().execute()

    event = sb.table("organiser_events").select(
        "name, industry_vertical, venue, start_date, end_date"
    ).eq("id", event_id).maybe_single().execute()

    organiser_name = organiser.data["name"] if organiser and organiser.data else "Organiser"

    # update link — mark accepted
    sb.table("organiser_exhibitor_links").update({
        "status":       "accepted",
        "exhibitor_id": org_id,
        "accepted_at":  datetime.utcnow().isoformat(),
    }).eq("invite_token", token).execute()

    # update organisation — link to organiser event
    sb.table("organisations").update({
        "organiser_event_id":    event_id,
        "is_organiser_managed":  True,
        "organiser_powered_label": f"Powered by {organiser_name}",
    }).eq("id", org_id).execute()

    # create the exhibitor's event shell pre-filled from organiser event
    if event and event.data:
        event_payload = {
            "id":                 str(uuid.uuid4()),
            "org_id":             org_id,
            "created_by":         user["user_id"],
            "name":               event.data["name"],
            "type":               "trade_fair",
            "type_label":         "Trade Fair",
            "industry_vertical":  event.data.get("industry_vertical", "general"),
            "venue":              event.data.get("venue", ""),
            "date_from":          event.data.get("start_date", None),
            "date_to":            event.data.get("end_date", None),
            "country":            "",
            "company":            "",
            "product":            "",
            "website":            "",
            "booth_size":         "",
            "iei_credits":        0,
            "organiser_event_id": event_id,
            "status":             "active",
        }
        sb.table("events").insert(event_payload).execute()

    return {
        "message":        "Successfully joined the event",
        "organiser_name": organiser_name,
        "event_name":     event.data["name"] if event and event.data else "",
    }

# ── Visitor Rows CRUD ─────────────────────────────────────────────────────────
class VisitorRowData(BaseModel):
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    email: Optional[str] = ""
    company: Optional[str] = ""
    job_title: Optional[str] = ""
    country: Optional[str] = ""
    city: Optional[str] = ""
    phone: Optional[str] = ""
    linkedin_url: Optional[str] = ""
    categories_interest: Optional[str] = ""
    primary_reason: Optional[str] = ""
    company_size: Optional[str] = ""
    incumbent_vendor: Optional[str] = ""


@router.get("/organiser/events/{event_id}/visitor-rows")
def list_visitor_rows(
    event_id: str,
    page: int = 1,
    page_size: int = 50,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # verify ownership
    event = sb.table("organiser_events").select("id").eq(
        "id", event_id
    ).eq("organiser_id", organiser_id).maybe_single().execute()
    if not event or not event.data:
        raise HTTPException(status_code=404, detail="Event not found")

    offset = (page - 1) * page_size
    rows = sb.table("organiser_visitor_rows").select(
        "id, raw_data, created_at"
    ).eq("organiser_event_id", event_id).order(
        "created_at", desc=False
    ).range(offset, offset + page_size - 1).execute()

    # get total count
    count_result = sb.table("organiser_visitor_rows").select(
        "id", count="exact"
    ).eq("organiser_event_id", event_id).execute()

    return {
        "rows":       rows.data or [],
        "total":      count_result.count or 0,
        "page":       page,
        "page_size":  page_size,
    }


@router.post("/organiser/events/{event_id}/visitor-rows")
def add_visitor_row(
    event_id: str,
    body: VisitorRowData,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # verify ownership
    event = sb.table("organiser_events").select("id").eq(
        "id", event_id
    ).eq("organiser_id", organiser_id).maybe_single().execute()
    if not event or not event.data:
        raise HTTPException(status_code=404, detail="Event not found")

    # check quota
    org = sb.table("organisers").select(
        "data_quota, data_used"
    ).eq("id", organiser_id).maybe_single().execute()
    if org and org.data and org.data["data_used"] >= org.data["data_quota"]:
        raise HTTPException(status_code=400, detail="Data quota reached")

    # find or create a manual upload record
    manual_upload = sb.table("organiser_visitor_uploads").select("id").eq(
        "organiser_event_id", event_id
    ).eq("filename", "__manual__").maybe_single().execute()

    if not manual_upload or not manual_upload.data:
        upload_result = sb.table("organiser_visitor_uploads").insert({
            "id":                 str(uuid.uuid4()),
            "organiser_id":       organiser_id,
            "organiser_event_id": event_id,
            "filename":           "__manual__",
            "row_count":          0,
        }).execute()
        upload_id = upload_result.data[0]["id"]
    else:
        upload_id = manual_upload.data["id"]

    # insert row
    row_id = str(uuid.uuid4())
    sb.table("organiser_visitor_rows").insert({
        "id":                 row_id,
        "upload_id":          upload_id,
        "organiser_event_id": event_id,
        "raw_data":           body.dict(),
    }).execute()

    # increment data_used and upload row_count
    if org and org.data:
        sb.table("organisers").update(
            {"data_used": org.data["data_used"] + 1}
        ).eq("id", organiser_id).execute()
    sb.table("organiser_visitor_uploads").update(
        {"row_count": sb.table("organiser_visitor_rows").select("id", count="exact").eq("upload_id", upload_id).execute().count}
    ).eq("id", upload_id).execute()

    return {"id": row_id, "message": "Row added"}


@router.patch("/organiser/visitor-rows/{row_id}")
def update_visitor_row(
    row_id: str,
    body: VisitorRowData,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    # verify the row belongs to this organiser via event
    row = sb.table("organiser_visitor_rows").select(
        "id, organiser_event_id"
    ).eq("id", row_id).maybe_single().execute()
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Row not found")

    event = sb.table("organiser_events").select("id").eq(
        "id", row.data["organiser_event_id"]
    ).eq("organiser_id", organiser_id).maybe_single().execute()
    if not event or not event.data:
        raise HTTPException(status_code=403, detail="Not authorized")

    sb.table("organiser_visitor_rows").update(
        {"raw_data": body.dict()}
    ).eq("id", row_id).execute()

    return {"message": "Row updated"}


@router.delete("/organiser/visitor-rows/{row_id}")
def delete_visitor_row(
    row_id: str,
    current_user: dict = Depends(get_current_organiser_user),
):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    row = sb.table("organiser_visitor_rows").select(
        "id, organiser_event_id"
    ).eq("id", row_id).maybe_single().execute()
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Row not found")

    event = sb.table("organiser_events").select("id").eq(
        "id", row.data["organiser_event_id"]
    ).eq("organiser_id", organiser_id).maybe_single().execute()
    if not event or not event.data:
        raise HTTPException(status_code=403, detail="Not authorized")

    sb.table("organiser_visitor_rows").delete().eq("id", row_id).execute()

    # decrement data_used
    org = sb.table("organisers").select("data_used").eq(
        "id", organiser_id
    ).maybe_single().execute()
    if org and org.data and org.data["data_used"] > 0:
        sb.table("organisers").update(
            {"data_used": org.data["data_used"] - 1}
        ).eq("id", organiser_id).execute()

    return {"message": "Row deleted"}


# ── Exhibitor-side: browse and import organiser visitor pool ──────────────────
@router.get("/organiser/pool/{organiser_event_id}")
async def get_organiser_pool(
    organiser_event_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 50,
):
    """Called by exhibitor app to browse available visitor rows."""
    from app.auth import get_current_user, get_user_org
    sb = get_supabase()
    try:
        user = await get_current_user(request)
        org_id = get_user_org(user["user_id"], sb)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    sb = get_supabase()

    # verify exhibitor is linked to this event
    link = sb.table("organiser_exhibitor_links").select(
        "id, data_allocation, data_consumed, status"
    ).eq("organiser_event_id", organiser_event_id).eq(
        "exhibitor_id", org_id
    ).maybe_single().execute()

    if not link or not link.data:
        raise HTTPException(status_code=403, detail="Not linked to this organiser event")

    if link.data["status"] == "removed":
        raise HTTPException(status_code=403, detail="Your access has been revoked")

    allocation  = link.data["data_allocation"]
    consumed    = link.data["data_consumed"]
    remaining   = max(0, allocation - consumed)

    offset = (page - 1) * page_size
    rows = sb.table("organiser_visitor_rows").select(
        "id, raw_data, created_at"
    ).eq("organiser_event_id", organiser_event_id).order(
        "created_at", desc=False
    ).range(offset, offset + page_size - 1).execute()

    count_result = sb.table("organiser_visitor_rows").select(
        "id", count="exact"
    ).eq("organiser_event_id", organiser_event_id).execute()

    return {
        "rows":        rows.data or [],
        "total":       count_result.count or 0,
        "page":        page,
        "page_size":   page_size,
        "allocation":  allocation,
        "consumed":    consumed,
        "remaining":   remaining,
        "link_id":     link.data["id"],
    }


@router.post("/organiser/import/{organiser_event_id}")
async def import_organiser_rows(
    organiser_event_id: str,
    body: dict,
    request: Request,
):
    """
    Import selected visitor rows from organiser pool into exhibitor's audience.
    body: { "row_ids": [...], "event_id": "exhibitor_event_id" }
    """
    from app.auth import get_current_user, get_user_org
    sb = get_supabase()
    try:
        user = await get_current_user(request)
        org_id = get_user_org(user["user_id"], sb)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    sb = get_supabase()

    row_ids      = body.get("row_ids", [])
    event_id     = body.get("event_id")

    if not row_ids:
        raise HTTPException(status_code=400, detail="No rows selected")
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id required")

    # verify link
    link = sb.table("organiser_exhibitor_links").select(
        "id, data_allocation, data_consumed, status"
    ).eq("organiser_event_id", organiser_event_id).eq(
        "exhibitor_id", org_id
    ).maybe_single().execute()

    if not link or not link.data:
        raise HTTPException(status_code=403, detail="Not linked to this organiser event")

    allocation = link.data["data_allocation"]
    consumed   = link.data["data_consumed"]
    remaining  = allocation - consumed

    if len(row_ids) > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"Selection ({len(row_ids)}) exceeds remaining allocation ({remaining})"
        )

    # fetch selected rows
    rows = sb.table("organiser_visitor_rows").select(
        "id, raw_data"
    ).in_("id", row_ids).execute()

    if not rows or not rows.data:
        raise HTTPException(status_code=404, detail="No rows found")

    # insert into audience_contacts
    imported = 0
    for row in rows.data:
        d = row["raw_data"] or {}
        first = d.get("first_name", "")
        last  = d.get("last_name", "")
        name  = f"{first} {last}".strip() or d.get("name", "Unknown")
        raw   = {
            "linkedin_url":        d.get("linkedin_url", ""),
            "categories_interest": d.get("categories_interest", ""),
            "primary_reason":      d.get("primary_reason", ""),
            "company_size":        d.get("company_size", ""),
            "incumbent_vendor":    d.get("incumbent_vendor", ""),
        }
        contact = {
            "id":          str(uuid.uuid4()),
            "event_id":    event_id,
            "name":        name,
            "email":       d.get("email", ""),
            "company":     d.get("company", ""),
            "designation": d.get("job_title", ""),
            "country":     d.get("country", ""),
            "city":        d.get("city", ""),
            "phone":       d.get("phone", ""),
            "raw_data":    raw,
            "source":      "organiser_import",
        }
        try:
            sb.table("audience_contacts").insert(contact).execute()
            imported += 1
        except Exception:
            pass  # skip duplicates

    # update consumed count
    sb.table("organiser_exhibitor_links").update(
        {"data_consumed": consumed + imported, "status": "active"}
    ).eq("id", link.data["id"]).execute()

    # log import
    sb.table("organiser_import_log").insert({
        "id":                 str(uuid.uuid4()),
        "organiser_event_id": organiser_event_id,
        "exhibitor_id":       org_id,
        "link_id":            link.data["id"],
        "rows_imported":      imported,
    }).execute()

    # update organiser data_used
    org_result = sb.table("organisers").select(
        "id, data_used"
    ).eq("id", sb.table("organiser_events").select(
        "organiser_id"
    ).eq("id", organiser_event_id).maybe_single().execute().data["organiser_id"]).maybe_single().execute()

    return {
        "message":  f"Successfully imported {imported} visitors",
        "imported": imported,
        "remaining": remaining - imported,
    }
