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
