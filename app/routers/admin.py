"""
Fingoh Super Admin — endpoints for managing customers, orgs and subscriptions.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import get_current_user
from app.database import get_db
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import secrets, string

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Super admin guard ─────────────────────────────────────────────────────────

def require_super_admin(current_user: dict = Depends(get_current_user)):
    db = get_db()
    result = db.table("profiles").select("is_super_admin").eq("id", current_user["user_id"]).single().execute()
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

    result = []
    for org in (orgs.data or []):
        # Get user count
        users = db.table("profiles").select("id,name,role").eq("org_id", org["id"]).execute()
        # Get event count
        events = db.table("events").select("id").eq("org_id", org["id"]).execute()
        result.append({
            **org,
            "user_count":  len(users.data or []),
            "event_count": len(events.data or []),
            "users":       users.data or [],
        })

    return result


# ── 2. Get single customer ────────────────────────────────────────────────────

@router.get("/customers/{org_id}")
async def get_customer(
    org_id: str,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()

    org = db.table("organisations").select("*").eq("id", org_id).single().execute()
    if not org.data:
        raise HTTPException(404, "Organisation not found")

    users  = db.table("profiles").select("*").eq("org_id", org_id).execute()
    events = db.table("events").select("id,name,date_from,date_to,created_at").eq("org_id", org_id).execute()

    return {
        **org.data,
        "users":  users.data or [],
        "events": events.data or [],
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
        "subscription_expires_at": payload.subscription_expires_at,
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

    return {
        "ok":       True,
        "org_id":   org_id,
        "user_id":  user_id,
        "email":    payload.admin_email,
        "password": password,  # shown once — admin must note it down
        "message":  f"Customer '{payload.company_name}' created successfully",
    }


# ── 4. Update customer ────────────────────────────────────────────────────────

@router.patch("/customers/{org_id}")
async def update_customer(
    org_id: str,
    payload: UpdateCustomerPayload,
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    db.table("organisations").update(updates).eq("id", org_id).execute()
    return {"ok": True, "updated": updates}


# ── 5. Disable / enable customer ─────────────────────────────────────────────

@router.patch("/customers/{org_id}/status")
async def set_customer_status(
    org_id: str,
    status: str = Query(..., regex="^(active|suspended|cancelled)$"),
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()
    db.table("organisations").update({"status": status}).eq("id", org_id).execute()
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
    profile = db.table("profiles").select("id").eq("org_id", org_id).eq("role", "admin").single().execute()
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

    return {"ok": True, "new_password": new_password}


# ── 7. Admin dashboard stats ──────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(
    current_user: dict = Depends(require_super_admin),
):
    db = get_db()

    orgs     = db.table("organisations").select("id,status,plan").neq("slug", "fingoh-admin").execute()
    profiles = db.table("profiles").select("id").neq("role", "super_admin").execute()
    events   = db.table("events").select("id").execute()
    contacts = db.table("audience_contacts").select("id").execute()

    orgs_data = orgs.data or []
    return {
        "total_customers":  len(orgs_data),
        "active_customers": sum(1 for o in orgs_data if o.get("status") == "active"),
        "total_users":      len(profiles.data or []),
        "total_events":     len(events.data or []),
        "total_contacts":   len(contacts.data or []),
        "plans": {
            "trial":      sum(1 for o in orgs_data if o.get("plan") == "trial"),
            "starter":    sum(1 for o in orgs_data if o.get("plan") == "starter"),
            "pro":        sum(1 for o in orgs_data if o.get("plan") == "pro"),
            "enterprise": sum(1 for o in orgs_data if o.get("plan") == "enterprise"),
        }
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
    org = db.table("organisations").select("id,name").eq("id", org_id).single().execute()
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
