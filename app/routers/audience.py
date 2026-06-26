from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import csv, io, httpx, os
from app.database import get_db
from app.auth import verify_token

router = APIRouter(prefix="/audience", tags=["audience"])
bearer = HTTPBearer()

MODAL_SCORER_URL = os.getenv("MODAL_SCORER_URL")


@router.post("/upload/{event_id}")
async def upload_audience(
    event_id: str,
    file: UploadFile = File(...),
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    verify_token(creds.credentials)
    supabase = get_db()

    content = await file.read()
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        rows = list(reader)
    except Exception:
        raise HTTPException(400, "Could not parse CSV")

    if not rows:
        raise HTTPException(400, "Empty CSV")

    scored = await _score_batch(rows)

    records = [
        {
            "event_id":    event_id,
            "name":        _get(r, "name"),
            "email":       _get(r, "email"),
            "company":     _get(r, "company"),
            "designation": _get(r, "designation"),
            "phone":       _get(r, "phone"),
            "city":        _get(r, "city"),
            "country":     _get(r, "country"),
            "raw_data":    r,
            "iei_score":   s["ieiScore"],
            "reg_prob":    s["regProb"],
            "scored_at":   "now()",
        }
        for r, s in zip(rows, scored)
    ]

    res = supabase.table("audience_contacts").upsert(
        records, on_conflict="event_id,email"
    ).execute()

    return {"uploaded": len(records), "event_id": event_id}


@router.get("/contacts/{event_id}")
async def list_contacts(
    event_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    verify_token(creds.credentials)
    supabase = get_db()
    res = (
        supabase.table("audience_contacts")
        .select("*")
        .eq("event_id", event_id)
        .order("iei_score", desc=True)
        .execute()
    )
    return res.data


async def _score_batch(rows: list[dict]) -> list[dict]:
    if not MODAL_SCORER_URL:
        # stub — returns mid-range scores so UI is exercised
        return [{"ieiScore": 50.0, "regProb": 0.5} for _ in rows]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(MODAL_SCORER_URL, json={"visitors": rows})
        resp.raise_for_status()
        return resp.json()["scores"]


def _get(row: dict, key: str) -> str | None:
    """Case-insensitive column lookup for messy CSV headers."""
    for k, v in row.items():
        if k.strip().lower() == key:
            return v or None
    return None