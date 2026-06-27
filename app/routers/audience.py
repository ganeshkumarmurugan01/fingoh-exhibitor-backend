from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
import csv, io, httpx, os, json, asyncio
from app.database import get_db
from app.auth import get_current_user

router = APIRouter(prefix="/audience", tags=["audience"])

MODAL_SCORER_URL  = os.getenv("MODAL_SCORER_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


# ── Fetch event + exhibitor context ──────────────────────────────────────────
def _get_event_context(supabase, event_id: str) -> dict:
    ev = supabase.table("events").select("*").eq("id", event_id).maybe_single().execute()
    if not ev or not ev.data:
        return {}
    cats = supabase.table("event_categories").select("category").eq("event_id", event_id).execute()
    context = ev.data.copy()
    context["categories"] = [c["category"] for c in (cats.data or [])]
    return context


# ── Claude enrichment — extracts signals for one visitor ─────────────────────
async def _enrich_visitor(visitor: dict, event_ctx: dict, client: httpx.AsyncClient) -> dict:
    if not ANTHROPIC_API_KEY:
        return {}

    name        = visitor.get("name") or f'{visitor.get("first_name","")} {visitor.get("last_name","")}'.strip()
    title       = visitor.get("job_title") or visitor.get("title") or visitor.get("designation") or ""
    company     = visitor.get("company") or ""
    country     = visitor.get("country") or ""
    reason      = visitor.get("primary_reason") or visitor.get("reason") or ""
    categories  = visitor.get("categories_interest") or ""

    ex_company   = event_ctx.get("company") or ""
    ex_product   = event_ctx.get("product") or ""
    ex_cats      = ", ".join(event_ctx.get("categories") or [])
    ex_icp_roles = ", ".join(event_ctx.get("roles") or [])
    ex_intent    = event_ctx.get("intent_why") or ""
    ex_buyers    = event_ctx.get("intent_buyers") or ""

    prompt = f"""You are an AI analyst for a B2B trade fair intelligence platform.

EXHIBITOR CONTEXT:
- Company: {ex_company}
- Product / solution: {ex_product}
- Target categories: {ex_cats}
- Target visitor roles: {ex_icp_roles}
- Exhibitor intent: {ex_intent}
- Ideal buyer profile: {ex_buyers}

VISITOR TO ANALYSE:
- Name: {name}
- Title: {title}
- Company: {company}
- Country: {country}
- Declared visit reason: {reason}
- Categories of interest: {categories}

Using your knowledge of this person's role, company, and industry context, estimate the following intent signals as decimal values between 0.0 and 1.0.

Respond ONLY with a valid JSON object — no explanation, no markdown:
{{
  "seniority_score": 0.0,
  "icp_fit_score": 0.0,
  "company_size_match": 0.0,
  "categories_specificity": 0.0,
  "buying_cycle_stage": 0.0,
  "trigger_event_score": 0.0,
  "tech_stack_compatibility": 0.0,
  "competitive_displacement": 0.0,
  "profile_completeness": 0.0,
  "enrichment_notes": "brief reason for scores"
}}

Rules:
- icp_fit_score: how well this visitor matches the exhibitor's ideal buyer (1.0 = perfect match, 0.0 = no match)
- seniority_score: buying authority (1.0 = CEO/CXO, 0.75 = Director/VP, 0.5 = Manager, 0.3 = Analyst)
- buying_cycle_stage: evidence of active evaluation (1.0 = active RFP/procurement, 0.5 = researching, 0.1 = awareness)
- trigger_event_score: recent company signals like funding, expansion, new hire (0-1)
- Be honest — if you have no data, use 0.3 as neutral, not 0.0"""

    try:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=25,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        # Strip markdown fences if present
        text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[enrichment] failed for {name}: {e}")
        return {}


# ── Score batch via Modal XGBoost ─────────────────────────────────────────────
async def _score_batch(rows: list[dict]) -> list[dict]:
    if not MODAL_SCORER_URL:
        return [{"ieiScore": 50.0, "regProb": 0.5} for _ in rows]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(MODAL_SCORER_URL, json={"visitors": rows})
        resp.raise_for_status()
        return resp.json()["scores"]


# ── Upload endpoint ───────────────────────────────────────────────────────────
@router.post("/upload/{event_id}")
async def upload_audience(
    event_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    supabase = get_db()

    content = await file.read()
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        rows = list(reader)
    except Exception:
        raise HTTPException(400, "Could not parse CSV")

    if not rows:
        raise HTTPException(400, "Empty CSV")

    # Fetch exhibitor context for this event
    event_ctx = _get_event_context(supabase, event_id)

    # Enrich each visitor with Claude (parallel, max 5 concurrent)
    enriched_rows = []
    if ANTHROPIC_API_KEY:
        async with httpx.AsyncClient() as client:
            sem = asyncio.Semaphore(5)
            async def enrich_one(row):
                async with sem:
                    signals = await _enrich_visitor(row, event_ctx, client)
                    return {**row, **signals}
            enriched_rows = await asyncio.gather(*[enrich_one(r) for r in rows])
    else:
        enriched_rows = rows

    # Score with XGBoost via Modal
    scored = await _score_batch(enriched_rows)

    records = [
        {
            "event_id":    event_id,
            "name":        _get(r, "name") or f'{_get(r, "first_name") or ""} {_get(r, "last_name") or ""}'.strip() or None,
            "email":       _get(r, "email"),
            "company":     _get(r, "company"),
            "designation": _get(r, "designation") or _get(r, "job_title"),
            "phone":       _get(r, "phone"),
            "city":        _get(r, "city"),
            "country":     _get(r, "country"),
            "raw_data":    r,
            "iei_score":   s["ieiScore"],
            "reg_prob":    s["regProb"],
            "scored_at":   "now()",
        }
        for r, s in zip(enriched_rows, scored)
    ]

    supabase.table("audience_contacts").upsert(
        records, on_conflict="event_id,email"
    ).execute()

    return {"uploaded": len(records), "event_id": event_id}


@router.get("/contacts/{event_id}")
async def list_contacts(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    supabase = get_db()
    res = (
        supabase.table("audience_contacts")
        .select("*")
        .eq("event_id", event_id)
        .order("iei_score", desc=True)
        .execute()
    )
    return res.data


def _get(row: dict, key: str) -> str | None:
    for k, v in row.items():
        if k.strip().lower() == key:
            return v or None
    return None


@router.post("/debug-enrich")
async def debug_enrich(payload: dict, current_user: dict = Depends(get_current_user)):
    """Debug: run enrichment on a single visitor and return raw signals."""
    event_ctx = payload.get("event_ctx", {})
    visitor = payload.get("visitor", {})
    async with httpx.AsyncClient() as client:
        signals = await _enrich_visitor(visitor, event_ctx, client)
    return {
        "anthropic_key_set": bool(ANTHROPIC_API_KEY),
        "signals": signals,
    }


@router.post("/debug-enrich")
async def debug_enrich(payload: dict, current_user: dict = Depends(get_current_user)):
    """Debug: run enrichment on a single visitor and return raw signals."""
    event_ctx = payload.get("event_ctx", {})
    visitor = payload.get("visitor", {})
    async with httpx.AsyncClient() as client:
        signals = await _enrich_visitor(visitor, event_ctx, client)
    return {
        "anthropic_key_set": bool(ANTHROPIC_API_KEY),
        "signals": signals,
    }
