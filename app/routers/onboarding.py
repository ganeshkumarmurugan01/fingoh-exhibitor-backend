import re
import secrets
import string
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from app.auth import get_current_user
from app.database import get_db
from app.models.organisation import (
    OrganisationCreate,
    OrganisationResponse,
    ProfileResponse,
    ProfileUpdate,
)

logger = logging.getLogger("fingoh.onboarding")

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


_DEFAULT_PLAN_FEATURES = {
    # Current plans
    "trial":   {"has_ai_features":True,  "has_crm_sync":False, "has_meeting_scheduler":False, "has_deep_iei":False, "has_walk_in_capture":True,  "max_contacts_per_event":100,   "max_deep_iei_per_event":10},
    "starter": {"has_ai_features":True,  "has_crm_sync":False, "has_meeting_scheduler":True,  "has_deep_iei":False, "has_walk_in_capture":True,  "max_contacts_per_event":500,   "max_deep_iei_per_event":50},
    "growth":  {"has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "max_contacts_per_event":2000,  "max_deep_iei_per_event":200},
    "scale":   {"has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "max_contacts_per_event":10000, "max_deep_iei_per_event":1000},
    # Legacy plan IDs — backward compat for existing orgs
    "single_event":     {"has_ai_features":True,  "has_crm_sync":False, "has_meeting_scheduler":True,  "has_deep_iei":False, "has_walk_in_capture":True,  "max_contacts_per_event":500,   "max_deep_iei_per_event":50},
    "event_bundle":     {"has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "max_contacts_per_event":1500,  "max_deep_iei_per_event":150},
    "annual_self_serve":{"has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "max_contacts_per_event":5000,  "max_deep_iei_per_event":500},
    "annual_enterprise":{"has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "max_contacts_per_event":10000, "max_deep_iei_per_event":1000},
}

def _get_plan_features(db, plan: str) -> dict:
    try:
        res = db.table("plan_configs").select("*").eq("plan_id", plan).maybe_single().execute()
        if res and res.data:
            d = res.data
            return {
                "has_ai_features":        d.get("has_ai_features", True),
                "has_crm_sync":           d.get("has_crm_sync", False),
                "has_meeting_scheduler":  d.get("has_meeting_scheduler", True),
                "has_deep_iei":           d.get("has_deep_iei", False),
                "has_walk_in_capture":    d.get("has_walk_in_capture", True),
                "max_contacts_per_event": d.get("max_contacts_per_event", 500),
                "max_deep_iei_per_event": d.get("max_deep_iei_per_event", 50),
            }
    except Exception:
        pass
    return _DEFAULT_PLAN_FEATURES.get(plan, _DEFAULT_PLAN_FEATURES["starter"])


@router.get("/me")
def get_my_profile(current_user: dict = Depends(get_current_user)):
    db = get_db()
    result = (
        db.table("profiles")
        .select("*")
        .eq("id", current_user["user_id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile = result.data[0]

    # Fetch org info including plan
    org_name = None
    plan = "trial"
    org_status = "active"
    if profile.get("org_id"):
        org_res = db.table("organisations").select("name,plan,status").eq("id", profile["org_id"]).maybe_single().execute()
        if org_res and org_res.data:
            org_name   = org_res.data.get("name")
            plan       = org_res.data.get("plan") or "trial"
            org_status = org_res.data.get("status") or "active"

    plan_features = _get_plan_features(db, plan)

    email = current_user.get("email") or ""
    return {**profile, "org_name": org_name, "email": email, "plan": plan, "org_status": org_status, "plan_features": plan_features}


@router.post("/organisation", response_model=OrganisationResponse, status_code=201)
def create_organisation(
    payload: OrganisationCreate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user["user_id"]

    # Check if user already has an org
    profile_result = (
        db.table("profiles")
        .select("org_id")
        .eq("id", user_id)
        .execute()
    )
    if profile_result.data and profile_result.data[0].get("org_id"):
        raise HTTPException(
            status_code=400,
            detail="User already belongs to an organisation",
        )

    # Check slug uniqueness
    org_data = payload.to_db()
    slug = org_data["slug"]
    existing = (
        db.table("organisations")
        .select("id")
        .eq("slug", slug)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=409,
            detail=f"Organisation name '{slug}' is already taken.",
        )

    # Create org
    org_result = db.table("organisations").insert(org_data).execute()
    if not org_result.data:
        raise HTTPException(status_code=500, detail="Failed to create organisation")
    org = org_result.data[0]

    # Link user profile to org
    db.table("profiles").update({
        "org_id": org["id"],
        "role": "admin",
    }).eq("id", user_id).execute()

    return org


# ── Public self-signup ────────────────────────────────────────────────────────

class SelfSignupPayload(BaseModel):
    name: str
    company: str
    email: EmailStr
    country: str
    password: str


@router.post("/signup", status_code=201)
async def self_signup(payload: SelfSignupPayload):
    """
    Public endpoint — no auth required.
    Creates a Supabase auth user + org + profile on the trial plan.
    """
    import httpx
    from app.config import get_settings
    settings = get_settings()

    db = get_db()

    admin_headers = {
        "apikey":        settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type":  "application/json",
    }

    # Derive a slug from company name
    slug = re.sub(r"[^a-z0-9]+", "-", payload.company.lower()).strip("-")[:40]
    # Ensure slug uniqueness
    existing = db.table("organisations").select("id").eq("slug", slug).execute()
    if existing.data:
        slug = f"{slug}-{secrets.token_hex(3)}"

    # Check email not already registered (email lives in auth.users, not profiles)
    async with httpx.AsyncClient(timeout=15) as client:
        lookup = await client.get(
            f"{settings.supabase_url}/auth/v1/admin/users",
            headers=admin_headers,
            params={"filter": payload.email, "per_page": 1},
        )
    if lookup.status_code == 200:
        users = lookup.json().get("users", [])
        if any(u.get("email", "").lower() == payload.email.lower() for u in users):
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # Create org on trial plan
    org_res = db.table("organisations").insert({
        "name":    payload.company,
        "slug":    slug,
        "plan":    "trial",
        "status":  "active",
        "max_events": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    if not org_res.data:
        raise HTTPException(status_code=500, detail="Failed to create organisation")

    org_id = org_res.data[0]["id"]

    # Create Supabase auth user
    admin_url = f"{settings.supabase_url}/auth/v1/admin/users"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(admin_url, headers=admin_headers, json={
            "email":         payload.email,
            "password":      payload.password,
            "email_confirm": False,   # user must verify via email link
            "user_metadata": {"name": payload.name, "org_id": org_id},
        })

    if r.status_code not in (200, 201):
        db.table("organisations").delete().eq("id", org_id).execute()
        err = r.json().get("msg") or r.json().get("message") or r.text[:200]
        raise HTTPException(status_code=400, detail=err)

    user_id = r.json()["id"]

    # Generate the Supabase verification link so we can send it in our own branded email
    verify_link = None
    async with httpx.AsyncClient(timeout=15) as client:
        gl = await client.post(
            f"{settings.supabase_url}/auth/v1/admin/generate_link",
            headers=admin_headers,
            json={"type": "signup", "email": payload.email, "password": payload.password},
        )
    if gl.status_code in (200, 201):
        verify_link = gl.json().get("action_link") or gl.json().get("properties", {}).get("action_link")

    # Create profile
    db.table("profiles").upsert({
        "id":    user_id,
        "org_id": org_id,
        "name":  payload.name,
        "role":  "admin",
        "title": "Account Admin",
    }).execute()

    # Send verification + welcome emails via Zoho using platform email config (best-effort)
    try:
        await _send_platform_emails(payload.email, payload.name, payload.company, verify_link, db)
    except Exception as e:
        logger.warning("Platform email send failed: %s", e)

    return {"ok": True, "org_id": org_id, "user_id": user_id}


async def _send_platform_emails(to_email: str, to_name: str, company: str, verify_link: str | None, db) -> None:
    import os, httpx
    from app.routers.meetings import get_zoho_access_token
    from app.routers.admin import get_platform_email_config_internal
    from app.routers.email_config import render_email_html

    ZOHO_ACCOUNT_ID = os.getenv("ZOHO_ACCOUNT_ID", "670863000000008002")
    ZOHO_FROM_EMAIL = os.getenv("ZOHO_FROM_EMAIL", "noreply@fingoh.ai")

    config = get_platform_email_config_internal(db)
    templates = config.get("templates", {})
    sender_name = config.get("sender_name") or "Fingoh"
    reply_to    = config.get("reply_to") or ZOHO_FROM_EMAIL

    extra_vars = {"name": to_name, "company": company, "verify_link": verify_link or "#"}

    access_token = await get_zoho_access_token()

    async def _zoho_send(subject: str, body_html: str):
        html = render_email_html(body_html, config, visitor_name=to_name, extra_vars=extra_vars)
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
                headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
                json={
                    "fromAddress": ZOHO_FROM_EMAIL,
                    "toAddress":   to_email,
                    "replyTo":     reply_to,
                    "subject":     subject,
                    "content":     html,
                    "mailFormat":  "html",
                },
            )

    # 1. Verification email (only if we have a link)
    if verify_link:
        verification_body = templates.get("signup_verification", "")
        await _zoho_send("Confirm your email — Fingoh", verification_body)

    # 2. Welcome email
    welcome_body = templates.get("trial_welcome", "")
    await _zoho_send("Welcome to Fingoh — your Free Trial is ready", welcome_body)


@router.patch("/me", response_model=ProfileResponse)
def update_my_profile(
    payload: ProfileUpdate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        db.table("profiles")
        .update(update_data)
        .eq("id", current_user["user_id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    return result.data[0]
