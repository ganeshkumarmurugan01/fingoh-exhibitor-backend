"""
Fingoh Super Admin — endpoints for managing customers, orgs and subscriptions.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import get_current_user
from app.database import get_db
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import secrets, string

logger = logging.getLogger("fingoh.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Super admin guard ─────────────────────────────────────────────────────────

def require_super_admin(current_user: dict = Depends(get_current_user)):
    db = get_db()
    result = db.table("profiles").select("is_super_admin").eq("id", current_user["user_id"]).maybe_single().execute()
    if not result.data or not result.data.get("is_super_admin"):
        raise HTTPException(403, "Super admin access required")
    return current_user


# ── Models ────────────────────────────────────────────────────────────────────

class CreateCustomerPayload(BaseModel):
    company_name: str
    slug: str
    admin_email: str
    admin_name: str
    plan: str = "starter"
    max_events: int = 3
    admin_notes: Optional[str] = None
    subscription_expires_at: Optional[str] = None


class UpdateCustomerPayload(BaseModel):
    status: Optional[str] = None
    plan: Optional[str] = None
    max_events: Optional[int] = None
    admin_notes: Optional[str] = None
    subscription_expires_at: Optional[str] = None


class PlanConfigPayload(BaseModel):
    plan_id: str
    label: str
    description: Optional[str] = None
    max_events: int = 1
    max_staff_seats: int = 3
    max_contacts_per_event: int = 500
    max_deep_iei_per_event: int = 50
    has_ai_features: bool = True
    has_crm_sync: bool = False
    has_meeting_scheduler: bool = True
    has_deep_iei: bool = False
    has_walk_in_capture: bool = True
    support_level: str = "email"
    price_inr: Optional[int] = None
    price_usd: Optional[int] = None
    is_active: bool = True
    sort_order: int = 0
    features_list: Optional[list] = None


# ── Helper: generate a random password ───────────────────────────────────────

def _generate_password(length: int = 12) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(chars) for _ in range(length))


# ── 1. List all customers ─────────────────────────────────────────────────────

@router.get("/customers")
async def list_customers(
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()

    orgs = db.table("organisations")\
        .select("*")\
        .neq("slug", "fingoh-admin")\
        .order("created_at", desc=True)\
        .execute()

    org_ids = [o["id"] for o in (orgs.data or [])]
    if not org_ids:
        return []

    # Batch fetch all profiles and events in 2 queries
    all_profiles = db.table("profiles").select("id,name,role,org_id").in_("org_id", org_ids).neq("role", "super_admin").execute()
    all_events   = db.table("events").select("id,org_id").in_("org_id", org_ids).execute()

    # Group by org_id
    profiles_by_org = {}
    for p in (all_profiles.data or []):
        profiles_by_org.setdefault(p["org_id"], []).append(p)

    events_by_org = {}
    for e in (all_events.data or []):
        events_by_org.setdefault(e["org_id"], []).append(e)

    result = []
    for org in (orgs.data or []):
        oid = org["id"]
        users  = profiles_by_org.get(oid, [])
        events = events_by_org.get(oid, [])
        result.append({
            **org,
            "user_count":  len(users),
            "event_count": len(events),
            "users":       users,
        })

    return result


# ── 2. Get single customer ────────────────────────────────────────────────────

@router.get("/customers/{org_id}")
async def get_customer(
    org_id: str,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()

    org = db.table("organisations").select("*").eq("id", org_id).maybe_single().execute()
    if not org.data:
        raise HTTPException(404, "Organisation not found")

    users  = db.table("profiles").select("*").eq("org_id", org_id).neq("role", "super_admin").execute()
    events = db.table("events").select("id,name,date_from,date_to,created_at").eq("org_id", org_id).execute()

    # Compute effective max events including add-ons
    from app.routers.events import PLAN_EVENT_LIMITS, _get_extra_events
    plan         = org.data.get("plan", "trial")
    base_max     = org.data.get("max_events") or PLAN_EVENT_LIMITS.get(plan, 1)
    extra_events = _get_extra_events(db, org_id)

    return {
        **org.data,
        "users":               users.data or [],
        "events":              events.data or [],
        "effective_max_events": base_max + extra_events,
        "extra_events_addons": extra_events,
    }


# ── 3. Create customer ────────────────────────────────────────────────────────

@router.post("/customers")
async def create_customer(
    payload: CreateCustomerPayload,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()
    import httpx
    from app.config import get_settings
    settings = get_settings()

    # Check slug uniqueness
    existing = db.table("organisations").select("id").eq("slug", payload.slug).execute()
    if existing.data:
        raise HTTPException(409, f"Slug '{payload.slug}' already taken")

    # Create organisation
    org_res = db.table("organisations").insert({
        "name":                    payload.company_name,
        "slug":                    payload.slug,
        "plan":                    payload.plan,
        "status":                  "active",
        "subscription_plan":       payload.plan,
        "max_events":              payload.max_events,
        "admin_notes":             payload.admin_notes,
        "subscription_expires_at": payload.subscription_expires_at or None,
        "created_by_admin":        True,
        "created_at":              datetime.now(timezone.utc).isoformat(),
    }).execute()

    if not org_res.data:
        raise HTTPException(500, "Failed to create organisation")

    org_id   = org_res.data[0]["id"]
    password = _generate_password()

    # Create Supabase auth user via Admin API
    admin_url = f"{settings.supabase_url}/auth/v1/admin/users"
    headers   = {
        "apikey":        settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type":  "application/json",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(admin_url, headers=headers, json={
            "email":            payload.admin_email,
            "password":         password,
            "email_confirm":    True,
            "user_metadata":    {"name": payload.admin_name, "org_id": org_id},
        })

    if r.status_code not in (200, 201):
        # Rollback org creation
        db.table("organisations").delete().eq("id", org_id).execute()
        raise HTTPException(500, f"Failed to create user: {r.text[:200]}")

    user_data = r.json()
    user_id   = user_data["id"]

    # Create profile
    db.table("profiles").upsert({
        "id":     user_id,
        "org_id": org_id,
        "name":   payload.admin_name,
        "role":   "admin",
        "title":  "Account Admin",
    }).execute()

    # Send welcome email
    email_sent = await _send_welcome_email(
        to_email=payload.admin_email,
        to_name=payload.admin_name,
        company=payload.company_name,
        password=password,
    )

    return {
        "ok":         True,
        "org_id":     org_id,
        "user_id":    user_id,
        "email":      payload.admin_email,
        "password":   password,
        "email_sent": email_sent,
        "message":    f"Customer '{payload.company_name}' created successfully",
    }


# ── 4. Update customer ────────────────────────────────────────────────────────

@router.patch("/customers/{org_id}")
async def update_customer(
    org_id: str,
    payload: UpdateCustomerPayload,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()

    # Capture old values before update (for change detection)
    old_res = db.table("organisations").select("plan,status,name").eq("id", org_id).maybe_single().execute()
    old = old_res.data or {}

    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    db.table("organisations").update(updates).eq("id", org_id).execute()

    # Auto-email if plan or status changed
    plan_changed   = "plan"   in updates and updates["plan"]   != old.get("plan")
    status_changed = "status" in updates and updates["status"] != old.get("status")

    if plan_changed or status_changed:
        try:
            profile = db.table("profiles").select("id,name,email").eq("org_id", org_id).eq("role", "admin").maybe_single().execute()
            if profile.data:
                new_plan   = updates.get("plan",   old.get("plan",""))
                new_status = updates.get("status", old.get("status","active"))
                company    = old.get("name", "")
                await _send_plan_change_email(
                    to_email=profile.data.get("email",""),
                    to_name=profile.data.get("name",""),
                    company=company,
                    new_plan=new_plan,
                    new_status=new_status,
                    plan_changed=plan_changed,
                    status_changed=status_changed,
                )
        except Exception as e:
            logger.error("Plan-change email failed: %s", e)

    return {"ok": True, "updated": updates}


# ── 5. Disable / enable customer ─────────────────────────────────────────────

@router.patch("/customers/{org_id}/status")
async def set_customer_status(
    org_id: str,
    status: str = Query(..., regex="^(active|suspended|cancelled)$"),
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()
    old_res = db.table("organisations").select("plan,status,name").eq("id", org_id).maybe_single().execute()
    old = old_res.data or {}
    db.table("organisations").update({"status": status}).eq("id", org_id).execute()
    if status != old.get("status"):
        try:
            profile = db.table("profiles").select("id,name,email").eq("org_id", org_id).eq("role", "admin").maybe_single().execute()
            if profile.data:
                await _send_plan_change_email(
                    to_email=profile.data.get("email",""),
                    to_name=profile.data.get("name",""),
                    company=old.get("name",""),
                    new_plan=old.get("plan",""),
                    new_status=status,
                    plan_changed=False,
                    status_changed=True,
                )
        except Exception as e:
            logger.error("Status-change email failed: %s", e)
    return {"ok": True, "status": status}


# ── 6. Reset customer password ────────────────────────────────────────────────

@router.post("/customers/{org_id}/reset-password")
async def reset_customer_password(
    org_id: str,
    current_user: dict = Depends(require_super_admin),
):
    db      = get_db()
    import httpx
    from app.config import get_settings
    settings = get_settings()

    # Get user for this org
    profile = db.table("profiles").select("id").eq("org_id", org_id).eq("role", "admin").maybe_single().execute()
    if not profile.data:
        raise HTTPException(404, "No admin user found for this org")

    user_id     = profile.data["id"]
    new_password = _generate_password()

    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
            headers={
                "apikey":        settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type":  "application/json",
            },
            json={"password": new_password},
        )

    if r.status_code not in (200, 201):
        raise HTTPException(500, f"Failed to reset password: {r.text[:200]}")

    # Get user email to send reset notification
    try:
        async with httpx.AsyncClient() as client:
            ur = await client.get(
                f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                headers={"apikey": settings.supabase_service_key, "Authorization": f"Bearer {settings.supabase_service_key}"},
            )
        user_email = ur.json().get("email", "")
        user_name  = profile.data.get("name", "Admin") if profile.data else "Admin"
        org = db.table("organisations").select("name").eq("id", org_id).maybe_single().execute()
        company = org.data.get("name", "") if org.data else ""
        if user_email:
            await _send_welcome_email(user_email, user_name, company, new_password)
    except Exception as e:
        logger.error("Reset email failed: %s", e)

    return {"ok": True, "new_password": new_password}


# ── 7. Admin dashboard stats ──────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()

    orgs     = db.table("organisations").select("id,status,plan").neq("slug", "fingoh-admin").execute()
    profiles = db.table("profiles").select("id", count="exact").or_("is_super_admin.eq.false,is_super_admin.is.null").execute()
    events   = db.table("events").select("id", count="exact").execute()
    contacts = db.table("audience_contacts").select("id", count="exact").execute()

    orgs_data = orgs.data or []
    return {
        "total_customers":  len(orgs_data),
        "active_customers": sum(1 for o in orgs_data if o.get("status") == "active"),
        "total_users":      profiles.count or 0,
        "total_events":     events.count or 0,
        "total_contacts":   contacts.count or 0,
        "plans": {p: sum(1 for o in orgs_data if o.get("plan") == p) for p in [
            "trial", "single_event", "event_bundle", "event_portfolio",
            "annual_self_serve", "annual_enterprise",
            "starter", "pro", "enterprise",  # legacy
        ]}
    }


class AddUserPayload(BaseModel):
    name: str
    email: str
    role: str = "user"
    title: Optional[str] = None


@router.post("/customers/{org_id}/users")
async def add_user_to_org(
    org_id: str,
    payload: AddUserPayload,
    current_user: dict = Depends(require_super_admin),
):
    """Add a new user to an existing organisation."""
    db = get_db()
    import httpx
    from app.config import get_settings
    settings = get_settings()

    # Verify org exists
    org = db.table("organisations").select("id,name").eq("id", org_id).maybe_single().execute()
    if not org.data:
        raise HTTPException(404, "Organisation not found")

    password = _generate_password()

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{settings.supabase_url}/auth/v1/admin/users",
            headers={
                "apikey":        settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type":  "application/json",
            },
            json={
                "email":         payload.email,
                "password":      password,
                "email_confirm": True,
                "user_metadata": {"name": payload.name, "org_id": org_id},
            },
        )

    if r.status_code not in (200, 201):
        raise HTTPException(500, f"Failed to create user: {r.text[:200]}")

    user_id = r.json()["id"]

    db.table("profiles").upsert({
        "id":     user_id,
        "org_id": org_id,
        "name":   payload.name,
        "role":   payload.role,
        "title":  payload.title or "",
    }).execute()

    return {
        "ok":       True,
        "user_id":  user_id,
        "email":    payload.email,
        "password": password,
    }


@router.get("/customers/{org_id}/users")
async def list_org_users(
    org_id: str,
    current_user: dict = Depends(require_super_admin),
):
    """List all users for an organisation with their auth emails."""
    db = get_db()
    import httpx
    from app.config import get_settings
    settings = get_settings()

    profiles = db.table("profiles").select("*").eq("org_id", org_id).execute()

    # Get emails from Supabase auth
    result = []
    for p in (profiles.data or []):
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{settings.supabase_url}/auth/v1/admin/users/{p['id']}",
                headers={
                    "apikey":        settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                },
            )
        email = r.json().get("email", "—") if r.status_code == 200 else "—"
        result.append({**p, "email": email})

    return result


# ── Helper: send welcome email with credentials ───────────────────────────────

async def _send_welcome_email(to_email: str, to_name: str, company: str, password: str) -> bool:
    """Send login credentials to new customer via Zoho Mail."""
    import os, httpx
    from app.routers.meetings import get_zoho_access_token

    ZOHO_ACCOUNT_ID = os.getenv("ZOHO_ACCOUNT_ID", "670863000000008002")
    ZOHO_FROM_EMAIL = os.getenv("ZOHO_FROM_EMAIL", "noreply@fingoh.ai")
    ZOHO_FROM_NAME  = os.getenv("ZOHO_FROM_NAME", "Fingoh")
    FRONTEND_URL    = os.getenv("FRONTEND_URL", "https://fingoh-exhibitor.vercel.app")

    html_body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 560px; margin: 0 auto; background: #ffffff;">
      <div style="background: #0D1B3E; padding: 28px 32px; border-radius: 12px 12px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 22px; font-weight: 800; letter-spacing: -0.04em;">Fingoh</h1>
        <p style="color: rgba(255,255,255,0.6); margin: 4px 0 0 0; font-size: 13px;">Exhibitor Intelligence Platform</p>
      </div>
      <div style="padding: 32px; background: #ffffff; border: 1px solid #E2E8F0; border-top: none; border-radius: 0 0 12px 12px;">
        <p style="font-size: 16px; color: #1E293B; margin: 0 0 8px 0;">Hi {to_name},</p>
        <p style="font-size: 14px; color: #475569; line-height: 1.6; margin: 0 0 24px 0;">
          Your Fingoh account for <strong>{company}</strong> has been set up. You can now log in and start managing your exhibition intelligence.
        </p>
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 20px 24px; margin-bottom: 24px;">
          <p style="font-size: 12px; font-weight: 600; color: #94A3B8; text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 12px 0;">Your Login Credentials</p>
          <p style="font-size: 13px; color: #1E293B; margin: 6px 0;"><strong>Email:</strong> {to_email}</p>
          <p style="font-size: 13px; color: #1E293B; margin: 6px 0;"><strong>Password:</strong> <code style="background: #E2E8F0; padding: 2px 8px; border-radius: 4px; font-size: 13px;">{password}</code></p>
          <p style="font-size: 11px; color: #DC2626; margin: 12px 0 0 0; font-weight: 600;">Please change your password after first login.</p>
        </div>
        <a href="{FRONTEND_URL}" style="display: inline-block; background: #0D1B3E; color: #ffffff; padding: 12px 28px; border-radius: 8px; text-decoration: none; font-size: 13px; font-weight: 700;">
          Login to Fingoh
        </a>
        <p style="font-size: 12px; color: #94A3B8; margin: 24px 0 0 0; line-height: 1.6;">
          If you have any questions, reply to this email or contact your Fingoh account manager.
        </p>
      </div>
    </div>
    """

    try:
        access_token = await get_zoho_access_token()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
                headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
                json={
                    "fromAddress": ZOHO_FROM_EMAIL,
                    "toAddress":   to_email,
                    "subject":     "Welcome to Fingoh - Your account is ready",
                    "content":     html_body,
                    "mailFormat":  "html",
                },
            )
        logger.info("Email response: %s %s", r.status_code, r.text[:300])
        return r.status_code == 200
    except Exception as e:
        logger.exception("Welcome email failed: %s", e)
        return False


@router.delete("/customers/{org_id}/users/{user_id}")
async def delete_user(
    org_id: str,
    user_id: str,
    current_user: dict = Depends(require_super_admin),
):
    """Delete a user from an organisation."""
    import httpx
    from app.config import get_settings
    settings = get_settings()
    db = get_db()

    # Delete from Supabase auth
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
            headers={
                "apikey":        settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
            },
        )

    # Delete profile regardless
    db.table("profiles").delete().eq("id", user_id).eq("org_id", org_id).execute()

    return {"ok": True}


@router.delete("/customers/{org_id}")
async def delete_customer(
    org_id: str,
    current_user: dict = Depends(require_super_admin),
):
    """Delete a customer organisation and all associated data."""
    import httpx
    from app.config import get_settings
    settings = get_settings()
    db = get_db()

    # Get all users in this org
    profiles = db.table("profiles").select("id").eq("org_id", org_id).execute()

    # Delete each user from Supabase auth
    for p in (profiles.data or []):
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{settings.supabase_url}/auth/v1/admin/users/{p['id']}",
                headers={
                    "apikey":        settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                },
            )

    # Delete all associated data
    # Get all events for this org
    events = db.table("events").select("id").eq("org_id", org_id).execute()
    event_ids = [e["id"] for e in (events.data or [])]

    for event_id in event_ids:
        db.table("audience_contacts").delete().eq("event_id", event_id).execute()
        db.table("meeting_requests").delete().eq("event_id", event_id).execute()
        db.table("conversation_signals").delete().eq("event_id", event_id).execute()
        db.table("crm_connections").delete().eq("event_id", event_id).execute()

    db.table("events").delete().eq("org_id", org_id).execute()
    db.table("profiles").delete().eq("org_id", org_id).execute()
    db.table("organisations").delete().eq("id", org_id).execute()

    return {"ok": True, "deleted": org_id}


from app.routers.utils import log_activity


@router.get("/customers/{org_id}/activity")
async def get_customer_activity(
    org_id: str,
    limit: int = 50,
    current_user: dict = Depends(require_super_admin),
):
    """Get activity log for a customer."""
    db = get_db()
    result = db.table("activity_logs")\
        .select("*")\
        .eq("org_id", org_id)\
        .order("created_at", desc=True)\
        .limit(limit)\
        .execute()
    return result.data or []


# ── Plan-change notification email ────────────────────────────────────────────

_PLAN_LABELS = {
    "trial":             "Trial",
    "single_event":      "Single Event",
    "event_bundle":      "Event Bundle",
    "event_portfolio":   "Event Portfolio",
    "annual_self_serve": "Annual · Self-serve",
    "annual_enterprise": "Annual · Enterprise",
}

_STATUS_MSG = {
    "active":    ("Account Activated", "Your Fingoh account is now active. You can log in and start creating events.", "#16A34A"),
    "suspended": ("Account Suspended", "Your Fingoh account has been temporarily suspended. Please contact support to restore access.", "#D97706"),
    "cancelled": ("Account Cancelled", "Your Fingoh subscription has been cancelled. Contact support if you believe this is an error.", "#DC2626"),
}

async def _send_plan_change_email(
    to_email: str, to_name: str, company: str,
    new_plan: str, new_status: str,
    plan_changed: bool, status_changed: bool,
) -> bool:
    import os, httpx
    from app.routers.meetings import get_zoho_access_token

    ZOHO_ACCOUNT_ID = os.getenv("ZOHO_ACCOUNT_ID", "670863000000008002")
    ZOHO_FROM_EMAIL = os.getenv("ZOHO_FROM_EMAIL", "noreply@fingoh.ai")
    FRONTEND_URL    = os.getenv("FRONTEND_URL", "https://exhibitor.fingoh.ai")

    if not to_email:
        return False

    plan_label = _PLAN_LABELS.get(new_plan, new_plan)
    status_subject, status_body, status_color = _STATUS_MSG.get(new_status, ("Account Update", "Your account has been updated.", "#0D1B3E"))

    if plan_changed and status_changed:
        subject = f"Your Fingoh plan has been updated — {plan_label}"
        body_msg = f"Your plan has been changed to <strong>{plan_label}</strong> and your account status is now <strong>{new_status}</strong>."
    elif plan_changed:
        subject = f"Your Fingoh plan has been updated — {plan_label}"
        body_msg = f"Your Fingoh subscription plan has been changed to <strong>{plan_label}</strong>."
    else:
        subject = status_subject
        body_msg = status_body

    html_body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 560px; margin: 0 auto; background: #ffffff;">
      <div style="background: #0D1B3E; padding: 28px 32px; border-radius: 12px 12px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 22px; font-weight: 800; letter-spacing: -0.04em;">Fingoh</h1>
        <p style="color: rgba(255,255,255,0.6); margin: 4px 0 0 0; font-size: 13px;">Exhibitor Intelligence Platform</p>
      </div>
      <div style="padding: 32px; background: #ffffff; border: 1px solid #E2E8F0; border-top: none; border-radius: 0 0 12px 12px;">
        <p style="font-size: 16px; color: #1E293B; margin: 0 0 8px 0;">Hi {to_name},</p>
        <p style="font-size: 14px; color: #475569; line-height: 1.6; margin: 0 0 24px 0;">
          {body_msg}
        </p>
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 4px solid {status_color}; border-radius: 10px; padding: 20px 24px; margin-bottom: 24px;">
          <p style="font-size: 12px; font-weight: 600; color: #94A3B8; text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 10px 0;">Account Summary</p>
          <p style="font-size: 13px; color: #1E293B; margin: 6px 0;"><strong>Company:</strong> {company}</p>
          <p style="font-size: 13px; color: #1E293B; margin: 6px 0;"><strong>Plan:</strong> {plan_label}</p>
          <p style="font-size: 13px; color: #1E293B; margin: 6px 0;"><strong>Status:</strong> <span style="color:{status_color};font-weight:700;">{new_status.capitalize()}</span></p>
        </div>
        <a href="{FRONTEND_URL}" style="display: inline-block; background: #0D1B3E; color: #ffffff; padding: 12px 28px; border-radius: 8px; text-decoration: none; font-size: 13px; font-weight: 700;">
          Go to Fingoh
        </a>
        <p style="font-size: 12px; color: #94A3B8; margin: 24px 0 0 0; line-height: 1.6;">
          Questions? Reply to this email or contact your Fingoh account manager.
        </p>
      </div>
    </div>
    """

    try:
        access_token = await get_zoho_access_token()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
                headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
                json={
                    "fromAddress": ZOHO_FROM_EMAIL,
                    "toAddress":   to_email,
                    "subject":     subject,
                    "content":     html_body,
                    "mailFormat":  "html",
                },
            )
        return r.status_code == 200
    except Exception as e:
        logger.exception("Plan-change email failed: %s", e)
        return False


# ── Plan configuration CRUD ────────────────────────────────────────────────────

# Default plan configs — used as fallback if DB table not yet populated
_DEFAULT_PLAN_CONFIGS = [
    {"plan_id":"trial",             "label":"Trial",               "description":"1 event, no commitment. Try Fingoh risk-free.",                       "max_events":1,   "max_staff_seats":2,  "max_contacts_per_event":200,   "max_deep_iei_per_event":20,   "has_ai_features":True,  "has_crm_sync":False, "has_meeting_scheduler":True,  "has_deep_iei":False, "has_walk_in_capture":True,  "support_level":"email",     "price_inr":0,      "price_usd":0,    "is_active":True,  "sort_order":0, "features_list":["1 event","Up to 200 contacts/event","Basic IEI scoring","Staff app","Email support"]},
    {"plan_id":"single_event",      "label":"Single Event",        "description":"1 event, pay per show. Best for first-time exhibitors.",               "max_events":1,   "max_staff_seats":3,  "max_contacts_per_event":500,   "max_deep_iei_per_event":50,   "has_ai_features":True,  "has_crm_sync":False, "has_meeting_scheduler":True,  "has_deep_iei":False, "has_walk_in_capture":True,  "support_level":"email",     "price_inr":25000,  "price_usd":299,  "is_active":True,  "sort_order":1, "features_list":["1 event","Up to 500 contacts/event","IEI scoring + tiers","Staff app","Walk-in capture","Meeting scheduler","Email support","Add-on events available"]},
    {"plan_id":"event_bundle",      "label":"Event Bundle",        "description":"5 events pre-purchased. Add extra events as needed.",                  "max_events":5,   "max_staff_seats":5,  "max_contacts_per_event":1500,  "max_deep_iei_per_event":150,  "has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "support_level":"priority",  "price_inr":100000, "price_usd":1199, "is_active":True,  "sort_order":2, "features_list":["5 events","Up to 1,500 contacts/event","Deep IEI Analysis (150/event)","CRM sync","Walk-in capture","Priority support","Add-on events + contacts available"]},
    {"plan_id":"event_portfolio",   "label":"Event Portfolio",     "description":"Retired plan — use Event Bundle or Annual instead.",                   "max_events":15,  "max_staff_seats":10, "max_contacts_per_event":3000,  "max_deep_iei_per_event":300,  "has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "support_level":"priority",  "price_inr":200000, "price_usd":2399, "is_active":False, "sort_order":3, "features_list":["Up to 15 events","Up to 3,000 contacts/event","Deep IEI Analysis (300/event)","CRM sync","All features","Priority support"]},
    {"plan_id":"annual_self_serve", "label":"Annual · Self-serve", "description":"5+ shows/year. Monthly or annual billing.",                            "max_events":999, "max_staff_seats":20, "max_contacts_per_event":5000,  "max_deep_iei_per_event":500,  "has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "support_level":"priority",  "price_inr":500000, "price_usd":5999, "is_active":True,  "sort_order":4, "features_list":["Unlimited events","Up to 5,000 contacts/event","Deep IEI Analysis (500/event)","CRM sync","All AI features","Priority support","Quarterly reviews"]},
    {"plan_id":"annual_enterprise", "label":"Annual · Enterprise", "description":"Unlimited events, dedicated support, custom integrations.",            "max_events":999, "max_staff_seats":999,"max_contacts_per_event":10000, "max_deep_iei_per_event":1000, "has_ai_features":True,  "has_crm_sync":True,  "has_meeting_scheduler":True,  "has_deep_iei":True,  "has_walk_in_capture":True,  "support_level":"dedicated", "price_inr":None,   "price_usd":None, "is_active":True,  "sort_order":5, "features_list":["Unlimited events","Up to 10,000 contacts/event","Deep IEI Analysis (1,000/event)","Dedicated CSM","Custom integrations","SLA support","Onboarding sessions"]},
]

# ── Add-on catalog ────────────────────────────────────────────────────────────

_DEFAULT_ADDON_CATALOG = [
    # Extra events
    {"addon_id":"extra_event_1",      "label":"Extra Event",             "addon_type":"extra_events",   "quantity":1,    "price_inr":12000, "price_usd":149, "description":"Add 1 additional event slot.",                         "is_active":True},
    # Extra contacts
    {"addon_id":"extra_contacts_100", "label":"Extra 100 Contacts",      "addon_type":"extra_contacts", "quantity":100,  "price_inr":1200,  "price_usd":15,  "description":"Add 100 extra contact slots to a specific event.",      "is_active":True},
    {"addon_id":"extra_contacts_200", "label":"Extra 200 Contacts",      "addon_type":"extra_contacts", "quantity":200,  "price_inr":2000,  "price_usd":25,  "description":"Add 200 extra contact slots to a specific event.",      "is_active":True},
    {"addon_id":"extra_contacts_500", "label":"Extra 500 Contacts",      "addon_type":"extra_contacts", "quantity":500,  "price_inr":4500,  "price_usd":55,  "description":"Add 500 extra contact slots to a specific event.",      "is_active":True},
    # Deep IEI analysis
    {"addon_id":"deep_iei_20",        "label":"Deep IEI — 20 analyses",  "addon_type":"extra_deep_iei", "quantity":20,   "price_inr":2500,  "price_usd":30,  "description":"Add 20 deep IEI research analyses to a specific event.","is_active":True},
    {"addon_id":"deep_iei_50",        "label":"Deep IEI — 50 analyses",  "addon_type":"extra_deep_iei", "quantity":50,   "price_inr":5500,  "price_usd":65,  "description":"Add 50 deep IEI research analyses to a specific event.","is_active":True},
    {"addon_id":"deep_iei_100",       "label":"Deep IEI — 100 analyses", "addon_type":"extra_deep_iei", "quantity":100,  "price_inr":9000,  "price_usd":110, "description":"Add 100 deep IEI research analyses to a specific event.","is_active":True},
]


@router.get("/plan-configs")
async def get_plan_configs(current_user: dict = Depends(require_super_admin)):
    """Return plan configurations — from DB if available, else defaults."""
    db = get_db()
    try:
        res = db.table("plan_configs").select("*").order("sort_order").execute()
        if res.data:
            return res.data
    except Exception:
        pass
    return _DEFAULT_PLAN_CONFIGS


@router.put("/plan-configs/{plan_id}")
async def upsert_plan_config(
    plan_id: str,
    payload: PlanConfigPayload,
    current_user: dict = Depends(require_super_admin),
):
    """Create or update a plan configuration."""
    db = get_db()
    data = {k: v for k, v in payload.model_dump().items()}
    data["plan_id"] = plan_id
    try:
        existing = db.table("plan_configs").select("id").eq("plan_id", plan_id).maybe_single().execute()
        if existing and existing.data:
            db.table("plan_configs").update(data).eq("plan_id", plan_id).execute()
        else:
            db.table("plan_configs").insert(data).execute()
    except Exception as e:
        raise HTTPException(500, f"Failed to save plan config: {e}")
    return {"ok": True, "plan_id": plan_id}


@router.post("/plan-configs/reset-defaults")
async def reset_plan_configs_to_defaults(current_user: dict = Depends(require_super_admin)):
    """Reset all plan configs to built-in defaults."""
    db = get_db()
    try:
        db.table("plan_configs").delete().neq("plan_id", "").execute()
        db.table("plan_configs").insert(_DEFAULT_PLAN_CONFIGS).execute()
    except Exception as e:
        raise HTTPException(500, f"Reset failed: {e}")
    return {"ok": True, "reset": len(_DEFAULT_PLAN_CONFIGS)}


# ── Add-on catalog endpoints ───────────────────────────────────────────────────

class AddonCatalogPayload(BaseModel):
    addon_id: str
    label: str
    addon_type: str  # 'extra_events' | 'extra_contacts'
    quantity: int
    price_inr: Optional[int] = None
    price_usd: Optional[int] = None
    description: Optional[str] = None
    is_active: bool = True


@router.get("/addon-catalog")
async def get_addon_catalog(current_user: dict = Depends(require_super_admin)):
    db = get_db()
    try:
        res = db.table("addon_catalog").select("*").order("addon_type").order("quantity").execute()
        if res.data:
            return res.data
    except Exception:
        pass
    return _DEFAULT_ADDON_CATALOG


@router.put("/addon-catalog/{addon_id}")
async def upsert_addon(
    addon_id: str,
    payload: AddonCatalogPayload,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()
    data = {k: v for k, v in payload.model_dump().items()}
    data["addon_id"] = addon_id
    try:
        existing = db.table("addon_catalog").select("id").eq("addon_id", addon_id).maybe_single().execute()
        if existing and existing.data:
            db.table("addon_catalog").update(data).eq("addon_id", addon_id).execute()
        else:
            db.table("addon_catalog").insert(data).execute()
    except Exception as e:
        raise HTTPException(500, f"Failed to save addon: {e}")
    return {"ok": True, "addon_id": addon_id}


@router.post("/addon-catalog/reset-defaults")
async def reset_addon_catalog(current_user: dict = Depends(require_super_admin)):
    db = get_db()
    try:
        db.table("addon_catalog").delete().neq("addon_id", "").execute()
        db.table("addon_catalog").insert(_DEFAULT_ADDON_CATALOG).execute()
    except Exception as e:
        raise HTTPException(500, f"Reset failed: {e}")
    return {"ok": True}


# ── Org add-on assignment ──────────────────────────────────────────────────────

class OrgAddonPayload(BaseModel):
    addon_type: str        # 'extra_events' | 'extra_contacts'
    quantity: int
    event_id: Optional[str] = None   # required when addon_type == 'extra_contacts'
    notes: Optional[str] = None
    addon_catalog_id: Optional[str] = None  # for reference


@router.get("/customers/{org_id}/addons")
async def list_org_addons(
    org_id: str,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()
    res = db.table("org_addons").select("*").eq("org_id", org_id).order("created_at", desc=True).execute()
    return res.data or []


@router.post("/customers/{org_id}/addons")
async def add_org_addon(
    org_id: str,
    payload: OrgAddonPayload,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()
    if payload.addon_type == "extra_contacts" and not payload.event_id:
        raise HTTPException(400, "event_id is required for extra_contacts add-ons")
    row = {
        "org_id":     org_id,
        "addon_type": payload.addon_type,
        "quantity":   payload.quantity,
        "event_id":   payload.event_id,
        "notes":      payload.notes,
        "addon_catalog_id": payload.addon_catalog_id,
    }
    res = db.table("org_addons").insert(row).execute()
    return res.data[0] if res.data else {"ok": True}


@router.delete("/customers/{org_id}/addons/{addon_row_id}")
async def remove_org_addon(
    org_id: str,
    addon_row_id: str,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()
    db.table("org_addons").delete().eq("id", addon_row_id).eq("org_id", org_id).execute()
    return {"ok": True}


def get_org_addon_totals(db, org_id: str, event_id: str) -> dict:
    """Return total extra_events and extra_contacts for this org/event from add-ons."""
    try:
        res = db.table("org_addons").select("addon_type,quantity,event_id").eq("org_id", org_id).execute()
        rows = res.data or []
        extra_events   = sum(r["quantity"] for r in rows if r["addon_type"] == "extra_events")
        extra_contacts = sum(r["quantity"] for r in rows if r["addon_type"] == "extra_contacts" and r.get("event_id") == event_id)
        return {"extra_events": extra_events, "extra_contacts": extra_contacts}
    except Exception:
        return {"extra_events": 0, "extra_contacts": 0}
