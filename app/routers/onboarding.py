from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user
from app.database import get_db
from app.models.organisation import (
    OrganisationCreate,
    OrganisationResponse,
    ProfileResponse,
    ProfileUpdate,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


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

    # Fetch org name
    org_name = None
    if profile.get("org_id"):
        org_res = db.table("organisations").select("name").eq("id", profile["org_id"]).maybe_single().execute()
        if org_res and org_res.data:
            org_name = org_res.data.get("name")

    return {**profile, "org_name": org_name}


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
