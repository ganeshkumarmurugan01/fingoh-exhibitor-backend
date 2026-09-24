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
        content = b64mod.b64decode(file_base64)
    except Exception:
        raise HTTPException(400, "Invalid base64 data.")

    if len(content) > LOGO_MAX_BYTES:
        raise HTTPException(400, "Logo too large. Max 2MB.")

    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "png"
    storage_path = f"{org_id}/{event_id}/logo/logo.{ext}"

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

    sb.table("events").update({"logo_url": logo_url}).eq("id", event_id).execute()

    return {"logo_url": logo_url}


# ── Banner upload ─────────────────────────────────────────────────────────────
class BannerUploadPayload(BaseModel):
    event_id: str
    file_base64: str
    file_name: str
    content_type: str

@router.post("/products/upload-banner")
async def upload_banner(payload: BannerUploadPayload, request: Request):
    user = await get_current_user(request)
    sb = get_sb()
    org_id = get_user_org(user["user_id"], sb)

    ALLOWED = ["image/jpeg", "image/png", "image/webp"]
    if payload.content_type not in ALLOWED:
        raise HTTPException(400, "Invalid file type. Use JPG, PNG or WEBP.")

    import base64 as b64mod
    try:
        content = b64mod.b64decode(file_base64)
    except Exception:
        raise HTTPException(400, "Invalid base64 data.")

    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "Banner too large. Max 5MB.")

    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "jpg"
    storage_path = f"{org_id}/{event_id}/banner/banner.{ext}"

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
    banner_url = signed.get("signedURL") or signed.get("signedUrl", "")

    sb.table("events").update({"banner_url": banner_url}).eq("id", event_id).execute()

    return {"banner_url": banner_url}


# ── Brochure PDF → Product catalog extraction ────────────────────────────────
@router.post("/products/extract-from-brochure")
async def extract_from_brochure(
    request: Request,
    file: UploadFile = File(...),
    event_id: str = Form(...),
):
    """
    Upload a product brochure PDF and extract structured product/service offerings
    using Claude. Returns a list of extracted offerings with category suggestions
    matched against the category_master table.
    """
    import anthropic, base64 as b64mod, os, json, re, logging
    logger = logging.getLogger("fingoh.products")

    user = await get_current_user(request)
    sb = get_sb()

    if file.content_type != "application/pdf":
        raise HTTPException(400, "Only PDF files are supported.")

    pdf_content = await file.read()
    if len(pdf_content) > 20 * 1024 * 1024:
        raise HTTPException(400, "Brochure too large. Max 20MB.")

    file_name = file.filename or "brochure.pdf"
    file_base64 = b64mod.b64encode(pdf_content).decode()

    # Fetch exhibitor event context
    event_res = sb.table("events").select("*").eq("id", event_id).maybe_single().execute()
    if not event_res or not event_res.data:
        raise HTTPException(404, "Event not found.")
    event = event_res.data

    # Fetch top-level category_master for context (L1 + L2 only to keep prompt lean)
    cats_res = sb.table("category_master").select("id,name,level,description,parent_id") \
        .eq("industry", "pharma").in_("level", [1, 2]).order("level").execute()
    categories = cats_res.data or []

    # Build category context string
    cat_lines = []
    l1_map = {c["id"]: c["name"] for c in categories if c["level"] == 1}
    for c in categories:
        if c["level"] == 1:
            cat_lines.append(f"[L1] {c['name']}: {c.get('description','')[:80]}")
        elif c["level"] == 2:
            parent = l1_map.get(c["parent_id"], "")
            cat_lines.append(f"  [L2] {c['name']} (under {parent}): {c.get('description','')[:80]}")
    cat_context = "\n".join(cat_lines[:80])  # cap at 80 lines

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""Analyse this product brochure for {event.get('company_name', 'a pharma company')} at {event.get('name', 'a trade fair')}.

Extract ALL distinct products/services. Return a JSON array only, no markdown, no explanation:
[
  {{
    "name": "exact product name (max 60 chars)",
    "type": "product|service|solution|spare",
    "short_description": "2-3 sentences on what it does, key benefits, use cases. Max 200 chars.",
    "key_specifications": ["spec1 (max 80 chars)", "spec2", "spec3"],
    "target_industries": ["Pharmaceutical", "Biotech"],
    "suggested_categories": ["L1 Category > L2 Category"]
  }}
]

Match suggested_categories from this list only:
{cat_context}

Rules:
- Keep ALL strings short — max 200 chars for descriptions, 80 chars for specs
- Max 4 key_specifications per product
- Max 2 suggested_categories per product  
- Extract every distinct product/service, typically 5-20 items
- Return ONLY the JSON array, starting with [ and ending with ]"""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=8000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": file_base64,
                        },
                        "title": file_name,
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        )

        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        raw = raw.strip()

        # Extract JSON array from response
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON array found in response")

        extracted = json.loads(json_match.group())

        # Match suggested_categories to actual category_master IDs
        all_cats_res = sb.table("category_master").select("id,name,level,parent_id") \
            .eq("industry", "pharma").execute()
        all_cats = all_cats_res.data or []
        cat_name_map = {c["name"].lower(): c for c in all_cats}

        for item in extracted:
            matched_cats = []
            for suggested in item.get("suggested_categories", []):
                parts = [p.strip() for p in suggested.split(">")]
                for part in reversed(parts):  # prefer L2/L3 match
                    match = cat_name_map.get(part.lower())
                    if match:
                        matched_cats.append({"id": match["id"], "name": match["name"], "level": match["level"]})
                        break
            item["category_master"] = matched_cats
            item.pop("suggested_categories", None)

        logger.info(f"[extract_brochure] Extracted {len(extracted)} offerings from {file_name}")
        return {
            "extracted": extracted,
            "count": len(extracted),
            "file_name": file_name
        }

    except json.JSONDecodeError as e:
        logger.error(f"[extract_brochure] JSON parse error: {e}\nRaw: {raw[:500]}")
        raise HTTPException(500, "Could not parse Claude response. Please try again.")
    except anthropic.BadRequestError as e:
        logger.error(f"[extract_brochure] Claude error: {e}")
        raise HTTPException(422, "Could not read PDF. Please ensure it contains selectable text.")
    except Exception as e:
        logger.error(f"[extract_brochure] Unexpected error: {e}")
        raise HTTPException(500, f"Extraction failed: {str(e)}")
