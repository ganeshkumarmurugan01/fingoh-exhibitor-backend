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


# ── Full IEI Research endpoint ────────────────────────────────────────────────
@router.post("/research/{contact_id}")
async def research_contact(
    contact_id: str,
    current_user: dict = Depends(get_current_user),
):
    supabase = get_db()

    # Fetch the contact
    contact_res = supabase.table("audience_contacts").select("*").eq("id", contact_id).maybe_single().execute()
    if not contact_res or not contact_res.data:
        raise HTTPException(404, "Contact not found")
    contact = contact_res.data

    # Fetch event context
    event_ctx = _get_event_context(supabase, contact["event_id"])

    # Build exhibition config
    ex_name    = event_ctx.get("name") or ""
    ex_industry = ", ".join(event_ctx.get("categories") or [])
    ex_desc    = event_ctx.get("product") or ""
    ex_company = event_ctx.get("company") or ""
    job_functions = event_ctx.get("roles") or []
    company_types = []
    custom_criteria = f"Exhibitor: {ex_company}. Products: {ex_desc}" if ex_company else ""

    weights = {"profileMatch": 25, "roleRelevance": 20, "companySignal": 25, "projectSpecificity": 20, "engagementIntent": 10}
    w = weights

    profile_criteria_lines = []
    if job_functions:
        profile_criteria_lines.append(f"Target job functions: {', '.join(job_functions)}")
    if company_types:
        profile_criteria_lines.append(f"Target company types: {', '.join(company_types)}")
    if custom_criteria:
        profile_criteria_lines.append(f"Additional criteria: {custom_criteria}")
    profile_criteria = "\n".join(profile_criteria_lines) or "No specific profile defined"

    raw = contact.get("raw_data") or {}
    visitor_name  = contact.get("name") or ""
    visitor_title = contact.get("designation") or ""
    visitor_co    = contact.get("company") or ""
    visitor_goals = raw.get("primary_reason") or raw.get("categories_interest") or ""
    visitor_loc   = contact.get("city") or contact.get("country") or ""

    prompt = f"""You are Fingoh.ai — an expert visitor intent intelligence system for B2B exhibitions.

EXHIBITION: {ex_name} | Sector: {ex_industry} | {ex_desc}

IDEAL VISITOR PROFILE:
{profile_criteria}

SCORING WEIGHTS:
- Profile match: {w['profileMatch']} pts max
- Role & seniority: {w['roleRelevance']} pts max
- Company signals: {w['companySignal']} pts max
- Project specificity: {w['projectSpecificity']} pts max
- Engagement intent: {w['engagementIntent']} pts max

CRITICAL SCORING RULES:
1. If visitor's industry/role does NOT match the ideal profile, score profile_match LOW (0-{round(w['profileMatch'] * 0.25)} pts).
2. Non-industry visitors (service providers, students, press) must score under 35 total.
3. Only strong profile matches with active sourcing needs should score 75+.

VISITOR:
Name: {visitor_name} | Title: {visitor_title} | Company: {visitor_co}
Goals: {visitor_goals} | Location: {visitor_loc}

Use web search to research this person and company. Look for recent news, funding rounds, product launches, hiring signals, press coverage, and any active procurement or sourcing projects.

Respond ONLY with valid JSON (no markdown):
{{
  "visitor": {{"name":"{visitor_name}","title":"{visitor_title}","company":"{visitor_co}","initials":"2 letters","intent_score":<0-100>,"intent_tier":"Hot|Warm|Cool|Cold","profile_match":"Strong match|Partial match|Weak match|No match","profile_match_reason":"one sentence"}},
  "intelligence_layers": [
    {{"layer":"Professional profile","color":"purple","signals":["3-4 signals"],"inference":"..."}},
    {{"layer":"Company intelligence","color":"teal","signals":["3-4 signals"],"inference":"..."}},
    {{"layer":"Key projects & initiatives","color":"amber","signals":["3-4 signals"],"inference":"..."}},
    {{"layer":"Need gap analysis","color":"red","signals":["2-3 signals"],"inference":"..."}}
  ],
  "synthesised_intent": "2-3 sentences on what they really want at {ex_name}",
  "intent_dimensions": [
    {{"type":"Primary","label":"Short label","tags":["tag1","tag2"],"description":"..."}},
    {{"type":"Secondary","label":"Short label","tags":["tag1"],"description":"..."}},
    {{"type":"Tertiary","label":"Short label","tags":["tag1"],"description":"..."}},
    {{"type":"Opportunistic","label":"Short label","tags":["tag1"],"description":"..."}}
  ],
  "score_breakdown": {{
    "profile_match_score":<0-{w['profileMatch']}>,"role_relevance_score":<0-{w['roleRelevance']}>,"company_signal_score":<0-{w['companySignal']}>,"project_specificity_score":<0-{w['projectSpecificity']}>,"engagement_intent_score":<0-{w['engagementIntent']}>,
    "profile_match_note":"...","role_note":"...","company_note":"...","project_note":"...","intent_note":"..."
  }},
  "exhibitor_matches": [
    {{"name":"...","type":"Zone · category at {ex_name}","match_score":<50-97>,"top_match":true,"reason":"..."}},
    {{"name":"...","type":"...","match_score":<40-88>,"top_match":false,"reason":"..."}},
    {{"name":"...","type":"...","match_score":<35-80>,"top_match":false,"reason":"..."}},
    {{"name":"...","type":"...","match_score":<30-75>,"top_match":false,"reason":"..."}}
  ],
  "exhibitor_brief": {{
    "context":"...","pain_points":"...","what_they_want":"...","dont_do":"...","opening_line":"..."
  }}
}}"""

    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 4000,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # Extract text from content blocks
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")

    if not text.strip():
        raise HTTPException(500, "Empty response from AI")

    # Parse JSON from response
    import re, json as jsonlib
    clean = re.sub(r"```json|```", "", text).strip()
    match = re.search(r"\{[\s\S]*\}", clean)
    if not match:
        raise HTTPException(500, "No JSON in AI response")

    return jsonlib.loads(match.group(0))


@router.post("/save-research/{contact_id}")
async def save_research(
    contact_id: str,
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    supabase = get_db()
    supabase.table("audience_contacts").update(
        {"iei_research": payload.get("iei_research")}
    ).eq("id", contact_id).execute()
    return {"ok": True}


# ── Staff App: Log on-site signal for a visitor ───────────────────────────────
@router.post("/log-signal/{event_id}")
async def log_signal(
    event_id: str,
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """
    Staff logs a conversation signal for a visitor.
    Payload: { email, conv_quality, questions_type, demo_attendance,
               collateral_requested, return_visit, notes }
    After saving, rescores the visitor via XGBoost.
    """
    supabase = get_db()

    email = payload.get("email")
    if not email:
        raise HTTPException(400, "email required")

    # Fetch existing contact
    res = (
        supabase.table("audience_contacts")
        .select("*")
        .eq("event_id", event_id)
        .eq("email", email)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(404, "Visitor not found")

    contact = res.data
    raw = contact.get("raw_data") or {}

    # Merge new signals into raw_data
    signal_fields = [
        "conv_quality", "questions_type", "demo_attendance",
        "collateral_requested", "return_visit", "notes",
        "badge_scan", "dwell_time_min",
    ]
    for field in signal_fields:
        if field in payload:
            raw[field] = payload[field]

    # Map signals to XGBoost feature names
    conv_quality    = float(payload.get("conv_quality", raw.get("conv_quality", 0)) or 0)
    questions_type  = payload.get("questions_type", raw.get("questions_type", "general"))
    demo_attendance = bool(payload.get("demo_attendance", raw.get("demo_attendance", False)))
    return_visit    = bool(payload.get("return_visit", raw.get("return_visit", False)))
    collateral      = payload.get("collateral_requested", raw.get("collateral_requested", "none"))

    # Build enriched visitor for rescoring
    enriched = {
        **raw,
        "conv_quality_score":   conv_quality / 5.0,
        "questions_type_score": 1.0 if questions_type in ["pricing","implementation"] else 0.7 if questions_type == "technical" else 0.5 if questions_type == "competitive" else 0.2,
        "demo_attendance":      1.0 if demo_attendance else 0.0,
        "return_visits":        1.0 if return_visit else 0.0,
        "collateral_specificity": 1.0 if collateral == "specific" else 0.3 if collateral == "generic" else 0.0,
        "badge_scan_count":     1.0,
        "icp_fit_score":        contact.get("raw_data", {}).get("icp_fit_score", 0.5),
        "seniority_score":      contact.get("raw_data", {}).get("seniority_score", 0.3),
        "buying_cycle_stage":   contact.get("raw_data", {}).get("buying_cycle_stage", 0.0),
    }

    # Rescore via Modal
    scored = await _score_batch([enriched])
    score = scored[0] if scored else {"ieiScore": contact.get("iei_score", 50), "regProb": contact.get("reg_prob", 0.5)}

    # Update contact
    supabase.table("audience_contacts").update({
        "raw_data":  raw,
        "iei_score": score["ieiScore"],
        "reg_prob":  score.get("regProb", 0.5),
        "scored_at": "now()",
    }).eq("id", contact["id"]).execute()

    return {
        "ok": True,
        "iei_score": score["ieiScore"],
        "iei_tier":  "Hot" if score["ieiScore"] >= 75 else "Warm" if score["ieiScore"] >= 50 else "Cool" if score["ieiScore"] >= 25 else "Cold",
        "reg_prob":  score.get("regProb", 0.5),
    }


@router.get("/visitors/{event_id}")
async def search_visitors(
    event_id: str,
    q: str = "",
    current_user: dict = Depends(get_current_user),
):
    """Search visitors by name, company, email for Staff App."""
    supabase = get_db()
    query = supabase.table("audience_contacts").select(
        "id,name,email,company,designation,city,country,iei_score,iei_tier,reg_prob,raw_data"
    ).eq("event_id", event_id)

    res = query.order("iei_score", desc=True).execute()
    contacts = res.data or []

    if q:
        q_lower = q.lower()
        contacts = [
            c for c in contacts
            if q_lower in (c.get("name") or "").lower()
            or q_lower in (c.get("company") or "").lower()
            or q_lower in (c.get("email") or "").lower()
        ]

    return contacts
