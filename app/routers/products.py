import os
import uuid
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from supabase import create_client, Client
from app.auth import get_current_user, get_user_org

router = APIRouter()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
BUCKET = "product-assets"

ALLOWED_TYPES = {
    "photo":    {"mimes": ["image/jpeg", "image/png", "image/webp"], "max_bytes": 5 * 1024 * 1024,  "max_count": 5},
    "video":    {"mimes": ["video/mp4", "video/quicktime"],           "max_bytes": 50 * 1024 * 1024, "max_count": 1},
    "brochure": {"mimes": ["application/pdf"],                        "max_bytes": 10 * 1024 * 1024, "max_count": 1},
}


def get_sb() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


@router.post("/products/upload-asset")
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    offering_id: str = Form(...),
    event_id: str = Form(...),
    asset_type: str = Form(...),
):
    user = await get_current_user(request)
    sb = get_sb()
    org_id = get_user_org(user["user_id"], sb)

    if asset_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Invalid asset_type. Must be one of: {list(ALLOWED_TYPES)}")

    rules = ALLOWED_TYPES[asset_type]

    if file.content_type not in rules["mimes"]:
        raise HTTPException(400, f"Invalid file type '{file.content_type}' for {asset_type}")

    content = await file.read()
    if len(content) > rules["max_bytes"]:
        max_mb = rules["max_bytes"] // (1024 * 1024)
        raise HTTPException(400, f"File too large. Max {max_mb}MB for {asset_type}")

    existing = sb.table("offering_assets") \
        .select("id", count="exact") \
        .eq("offering_id", offering_id) \
        .eq("asset_type", asset_type) \
        .execute()
    current_count = existing.count or 0
    if current_count >= rules["max_count"]:
        raise HTTPException(400, f"Max {rules['max_count']} {asset_type}(s) per offering")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "bin"
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    storage_path = f"{org_id}/{event_id}/{offering_id}/{unique_name}"

    try:
        sb.storage.from_(BUCKET).upload(
            path=storage_path,
            file=content,
            file_options={"content-type": file.content_type, "upsert": "false"},
        )
    except Exception as e:
        raise HTTPException(500, f"Storage upload failed: {str(e)}")

    signed = sb.storage.from_(BUCKET).create_signed_url(storage_path, 315_360_000)
    public_url = signed.get("signedURL") or signed.get("signedUrl", "")

    row = {
        "offering_id":     offering_id,
        "event_id":        event_id,
        "org_id":          org_id,
        "asset_type":      asset_type,
        "file_name":       file.filename,
        "storage_path":    storage_path,
        "public_url":      public_url,
        "file_size_bytes": len(content),
    }
    result = sb.table("offering_assets").insert(row).execute()
    if not result.data:
        raise HTTPException(500, "DB insert failed")

    return result.data[0]


@router.get("/products/{offering_id}/assets")
async def list_assets(offering_id: str, request: Request):
    await get_current_user(request)
    sb = get_sb()
    result = sb.table("offering_assets") \
        .select("*") \
        .eq("offering_id", offering_id) \
        .order("created_at") \
        .execute()
    return result.data or []


@router.delete("/products/asset/{asset_id}")
async def delete_asset(asset_id: str, request: Request):
    user = await get_current_user(request)
    sb = get_sb()
    org_id = get_user_org(user["user_id"], sb)

    row = sb.table("offering_assets") \
        .select("*") \
        .eq("id", asset_id) \
        .eq("org_id", org_id) \
        .maybe_single() \
        .execute()
    if not row or not row.data:
        raise HTTPException(404, "Asset not found or not owned by your org")

    asset = row.data

    try:
        sb.storage.from_(BUCKET).remove([asset["storage_path"]])
    except Exception as e:
        print(f"[WARN] Storage delete failed for {asset['storage_path']}: {e}")

    sb.table("offering_assets").delete().eq("id", asset_id).execute()
    return {"deleted": True, "id": asset_id}


@router.get("/products/event/{event_id}/asset-counts")
async def asset_counts_by_event(event_id: str, request: Request):
    await get_current_user(request)
    sb = get_sb()
    result = sb.table("offering_assets") \
        .select("offering_id, asset_type") \
        .eq("event_id", event_id) \
        .execute()

    counts: dict = {}
    for row in (result.data or []):
        oid = row["offering_id"]
        atype = row["asset_type"]
        if oid not in counts:
            counts[oid] = {"photo": 0, "video": 0, "brochure": 0}
        counts[oid][atype] += 1

    return counts


# ── Logo upload ──────────────────────────────────────────────────────────────
LOGO_ALLOWED_MIMES = ["image/jpeg", "image/png", "image/webp", "image/svg+xml"]
LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2MB

class LogoUploadPayload(BaseModel):
    event_id: str
    file_base64: str
    file_name: str
    content_type: str

@router.post("/products/upload-logo")
async def upload_logo(payload: LogoUploadPayload, request: Request):
    user = await get_current_user(request)
    sb = get_sb()
    org_id = get_user_org(user["user_id"], sb)

    if payload.content_type not in LOGO_ALLOWED_MIMES:
        raise HTTPException(400, "Invalid file type. Use JPG, PNG, WEBP or SVG.")

    import base64 as b64mod
    try:
        content = b64mod.b64decode(payload.file_base64)
    except Exception:
        raise HTTPException(400, "Invalid base64 data.")

    if len(content) > LOGO_MAX_BYTES:
        raise HTTPException(400, "Logo too large. Max 2MB.")

    ext = payload.file_name.rsplit(".", 1)[-1].lower() if "." in payload.file_name else "png"
    storage_path = f"{org_id}/{payload.event_id}/logo/logo.{ext}"

    try:
        sb.storage.from_(BUCKET).remove([storage_path])
    except:
        pass

    try:
        sb.storage.from_(BUCKET).upload(
            path=storage_path,
            file=content,
            file_options={"content-type": payload.content_type, "upsert": "true"},
        )
    except Exception as e:
        raise HTTPException(500, f"Storage upload failed: {str(e)}")

    signed = sb.storage.from_(BUCKET).create_signed_url(storage_path, 315_360_000)
    logo_url = signed.get("signedURL") or signed.get("signedUrl", "")

    sb.table("events").update({"logo_url": logo_url}).eq("id", payload.event_id).execute()

    return {"logo_url": logo_url}
