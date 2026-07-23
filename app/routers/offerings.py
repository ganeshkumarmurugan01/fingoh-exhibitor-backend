from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user
from app.database import get_db
from typing import Optional
from pydantic import BaseModel

router = APIRouter(prefix="/offerings", tags=["offerings"])

class OfferingCreate(BaseModel):
    type: str
    name: str
    category: Optional[list[str]] = None
    short_description: Optional[str] = None
    key_specifications: Optional[list[str]] = []
    target_industries: Optional[list[str]] = []
    display_order: Optional[int] = 0

class OfferingUpdate(BaseModel):
    type: Optional[str] = None
    name: Optional[str] = None
    category: Optional[list[str]] = None
    short_description: Optional[str] = None
    key_specifications: Optional[list[str]] = None
    target_industries: Optional[list[str]] = None
    display_order: Optional[int] = None

@router.get("/event/{event_id}")
def get_event_offerings(event_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    result = db.table("event_offerings").select("*").eq("event_id", event_id).order("display_order").execute()
    return result.data or []

@router.post("/event/{event_id}")
def create_offering(event_id: str, payload: OfferingCreate, current_user: dict = Depends(get_current_user)):
    db = get_db()
    # Get org_id from profile
    profile = db.table("profiles").select("org_id").eq("id", current_user["user_id"]).maybe_single().execute()
    if not profile or not profile.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    org_id = profile.data["org_id"]

    # Check offering count (max 5 per event)
    count_res = db.table("event_offerings").select("id", count="exact").eq("event_id", event_id).execute()
    if count_res.count and count_res.count >= 5:
        raise HTTPException(status_code=400, detail="Maximum 5 offerings per event")

    data = {
        "event_id": event_id,
        "org_id": org_id,
        **payload.model_dump()
    }
    result = db.table("event_offerings").insert(data).execute()
    return result.data[0]

@router.patch("/{offering_id}")
def update_offering(offering_id: str, payload: OfferingUpdate, current_user: dict = Depends(get_current_user)):
    db = get_db()
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    result = db.table("event_offerings").update(updates).eq("id", offering_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Offering not found")
    return result.data[0]

@router.delete("/{offering_id}")
def delete_offering(offering_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    db.table("event_offerings").delete().eq("id", offering_id).execute()
    return {"ok": True}

@router.get("/event/{event_id}/public")
def get_event_offerings_public(event_id: str):
    """Public endpoint for visitor registration - no auth required"""
    db = get_db()
    result = db.table("event_offerings").select("*").eq("event_id", event_id).order("display_order").execute()
    return result.data or []
