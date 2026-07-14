from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user, get_user_org
from app.database import get_db
from app.models.staff import (
    StaffCreate,
    StaffUpdate,
    StaffResponse,
    StaffLoginRequest,
    StaffLoginResponse,
)
from typing import List

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get("", response_model=List[StaffResponse])
def list_staff(current_user: dict = Depends(get_current_user)):
    """
    Returns the full org-level staff roster.
    Shown in the My Team panel on the Event Home screen.
    """
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)

    result = (
        db.table("staff")
        .select("*")
        .eq("org_id", org_id)
        .order("name")
        .execute()
    )
    return result.data or []


@router.post("", response_model=StaffResponse, status_code=201)
def add_staff(
    payload: StaffCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    Add a new staff member to the org roster.
    Email must be unique within the org.
    """
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)

    # Check for duplicate email within org
    existing = (
        db.table("staff")
        .select("id")
        .eq("org_id", org_id)
        .eq("email", payload.email)
        .maybe_single()
        .execute()
    )
    if existing and existing.data:
        raise HTTPException(
            status_code=409,
            detail=f"Staff member with email '{payload.email}' already exists in this organisation.",
        )

    insert_data = {
        "org_id": org_id,
        "name": payload.name,
        "email": str(payload.email),
        "title": payload.title,
        "responsibility": payload.responsibility,
    }
    if payload.passcode:
        insert_data["passcode"] = payload.passcode
    result = db.table("staff").insert(insert_data).execute()

    return result.data[0]


@router.patch("/{staff_id}", response_model=StaffResponse)
def update_staff(
    staff_id: str,
    payload: StaffUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update a staff member's title or responsibility."""
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    _get_staff_or_404(staff_id, org_id, db)

    update_data = {k: v for k, v in payload.dict().items() if v is not None and k != "passcode"}
    if payload.passcode is not None:
        update_data["passcode"] = payload.passcode or None  # allow clearing by passing ""
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        db.table("staff")
        .update(update_data)
        .eq("id", staff_id)
        .eq("org_id", org_id)
        .execute()
    )
    return result.data[0]


@router.delete("/{staff_id}", status_code=204)
def remove_staff(
    staff_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Remove a staff member from the org roster."""
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)
    _get_staff_or_404(staff_id, org_id, db)

    db.table("staff").delete().eq("id", staff_id).eq("org_id", org_id).execute()


@router.post("/verify-login", response_model=StaffLoginResponse)
def verify_staff_login(payload: StaffLoginRequest):
    db = get_db()

    event_result = (
        db.table("events")
        .select("org_id")
        .eq("id", payload.event_id)
        .maybe_single()
        .execute()
    )
    if not event_result or not event_result.data:
        raise HTTPException(status_code=404, detail="Event not found")

    org_id = event_result.data["org_id"]

    staff_result = (
        db.table("staff")
        .select("*")
        .eq("org_id", org_id)
        .eq("email", str(payload.email))
        .maybe_single()
        .execute()
    )
    if not staff_result or not staff_result.data:
        raise HTTPException(
            status_code=404,
            detail="Email not found in the staff roster for this event. Ask your manager to add you in My Team.",
        )

    staff = staff_result.data
    stored_passcode = staff.get("passcode")
    if stored_passcode:
        if not payload.passcode or payload.passcode.strip() != stored_passcode.strip():
            raise HTTPException(status_code=401, detail="Incorrect passcode")

    return {**staff, "event_id": payload.event_id, "passcode_required": bool(stored_passcode)}


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_staff_or_404(staff_id: str, org_id: str, db) -> dict:
    result = (
        db.table("staff")
        .select("*")
        .eq("id", staff_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return result.data
