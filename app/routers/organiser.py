# routers/organiser.py
# Fingoh Organiser Module — Phase 1: Auth + Account management

import os
import uuid
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client

router = APIRouter(prefix="/organiser", tags=["organiser"])

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
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        if payload.get("type") != "organiser":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


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
@router.post("/login")
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


@router.get("/me")
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
@router.post("/events")
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


@router.get("/events")
def list_organiser_events(current_user: dict = Depends(get_current_organiser_user)):
    sb = get_supabase()
    organiser_id = current_user["organiser_id"]

    result = sb.table("organiser_events").select("*").eq(
        "organiser_id", organiser_id
    ).order("created_at", desc=True).execute()

    return result.data or []


@router.get("/events/{event_id}")
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


@router.patch("/events/{event_id}")
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
@router.post("/admin/create-organiser")
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