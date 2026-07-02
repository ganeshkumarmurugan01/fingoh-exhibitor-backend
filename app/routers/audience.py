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


def _parse_meeting_interest(val):
    """Parse meeting_interest field from CSV — accepts yes/no/true/false/1/0."""
    if val is None:
        return None
    v = str(val).strip().lower()
    if v in ("yes","true","1","y"):
        return True
    if v in ("no","false","0","n"):
        return False
    return None

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
            "meeting_interest": _parse_meeting_interest(_get(r, "meeting_interest")),
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
    contacts = res.data or []

    # Get contact IDs that have signals for THIS event only
    sigs_res = (
        supabase.table("conversation_signals")
        .select("contact_id")
        .eq("event_id", event_id)
        .execute()
    )
    contacts_with_signals = {s["contact_id"] for s in (sigs_res.data or [])}

    # Null out onsite scores for contacts without current-event signals
    for c in contacts:
        if c["id"] not in contacts_with_signals:
            c["onsite_iei_score"] = None
            c["onsite_iei_tier"]  = None

    # Get meeting requests for this event, most recent per contact, for the
    # Live Dashboard "Meeting Status" column.
    meetings_res = (
        supabase.table("meeting_requests")
        .select("id, contact_id, status, proposed_datetime, duration_minutes, "
                "location, topic, notes, staff_completion_notes, completed_at, created_at, "
                "actual_start_time, actual_end_time, ai_analysis")
        .eq("event_id", event_id)
        .order("created_at", desc=True)
        .execute()
    )
    meeting_by_contact = {}
    for m in (meetings_res.data or []):
        # keep only the most recent meeting per contact (results are already
        # ordered created_at desc, so first occurrence wins)
        meeting_by_contact.setdefault(m["contact_id"], m)

    for c in contacts:
        m = meeting_by_contact.get(c["id"])
        c["meeting_status"] = m["status"] if m else None
        c["meeting"] = m

    return contacts


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


# ── Onsite IEI adjustment (deterministic multiplier) ────────────────────────────
def onsite_adjust(pre_event_iei: float, signals: dict) -> float:
    """Apply onsite signal multiplier to pre-event IEI score."""
    cq = signals.get("conv_quality_score")
    if cq is None:
        return pre_event_iei

    demo        = float(signals.get("demo_attendance", 0))
    return_v    = float(signals.get("return_visits", 0))
    meeting     = float(signals.get("proposal_demo_request", 0))
    badge       = float(signals.get("badge_scan_count", 0))
    urgency_buy = float(signals.get("buying_cycle_stage", 0.5))
    collateral  = float(signals.get("collateral_specificity", 0))

    if cq < 0.2:    base_mult = 0.35
    elif cq < 0.4:  base_mult = 0.55
    elif cq < 0.6:  base_mult = 0.75
    elif cq < 0.8:  base_mult = 0.90
    else:           base_mult = 1.05

    # Bonus signals — capped at +15 total to avoid score inflation
    # Each signal nudges the score but can't push a mid-tier visitor to 100
    raw_bonus = (
        demo        * 4  +
        return_v    * 5  +
        meeting     * 6  +
        badge       * 2  +
        collateral  * 3  +
        (urgency_buy - 0.5) * 5
    )
    bonus = min(raw_bonus, 15.0)  # hard cap on bonus
    return max(0.0, min(100.0, (pre_event_iei * base_mult) + bonus))


# ── Staff App: Log on-site signal for a visitor ───────────────────────────────
@router.post("/log-signal/{event_id}")
async def log_signal(
    event_id: str,
    payload: dict,
):
    """
    Staff logs a conversation signal for a visitor.
    Payload: { email, conv_quality, questions_type, demo_attendance,
               collateral_requested, return_visit, notes }
    After saving, rescores the visitor via XGBoost.
    """
    supabase = get_db()

    email = payload.get("email")
    contact_id = payload.get("contact_id")

    # Fetch existing contact by contact_id or email
    if contact_id:
        res = (
            supabase.table("audience_contacts")
            .select("*")
            .eq("id", contact_id)
            .maybe_single()
            .execute()
        )
    elif email:
        res = (
            supabase.table("audience_contacts")
            .select("*")
            .eq("event_id", event_id)
            .eq("email", email)
            .maybe_single()
            .execute()
        )
    else:
        raise HTTPException(400, "contact_id or email required")

    if not res or not res.data:
        raise HTTPException(404, "Visitor not found")

    contact = res.data
    return await apply_onsite_signal(supabase, event_id, contact, payload)


# ── Shared onsite scoring + signal logging ──────────────────────────────────
# Used by both the Staff App's log-signal flow (in-conversation signals) and
# the Meetings "mark complete" flow (a completed meeting is itself a strong
# onsite signal, even when the visitor never had a separate logged
# conversation — e.g. they went straight into a scheduled meeting).
async def apply_onsite_signal(supabase, event_id: str, contact: dict, payload: dict) -> dict:
    raw = contact.get("raw_data") or {}

    # Merge new signals into raw_data
    # Normalise field names from both Staff App and old format
    if "conversation_quality" in payload: raw["conv_quality"] = payload["conversation_quality"]
    if "conv_quality" in payload: raw["conv_quality"] = payload["conv_quality"]
    if "question_types" in payload: raw["questions_type"] = payload["question_types"][0] if isinstance(payload["question_types"], list) and payload["question_types"] else payload.get("question_types","general")
    if "questions_type" in payload: raw["questions_type"] = payload["questions_type"]
    if "demo_requested" in payload: raw["demo_attendance"] = payload["demo_requested"]
    if "demo_attendance" in payload: raw["demo_attendance"] = payload["demo_attendance"]
    if "collateral" in payload: raw["collateral_requested"] = payload["collateral"]
    if "collateral_requested" in payload: raw["collateral_requested"] = payload["collateral_requested"]
    if "return_visit" in payload: raw["return_visit"] = payload["return_visit"]
    if "notes" in payload: raw["notes"] = payload["notes"]
    if "badge_scan" in payload: raw["badge_scan"] = payload["badge_scan"]
    if "urgency" in payload: raw["urgency"] = payload["urgency"]
    if "buying_group" in payload: raw["buying_group"] = payload["buying_group"]
    if "meeting_booked" in payload: raw["meeting_booked"] = payload["meeting_booked"]

    # Map signals to XGBoost feature names
    conv_quality    = float(payload.get("conversation_quality") or payload.get("conv_quality") or raw.get("conv_quality", 0) or 0)
    questions_type  = payload.get("question_types", payload.get("questions_type", raw.get("questions_type", "general")))
    if isinstance(questions_type, list): questions_type = questions_type[0] if questions_type else "general"
    demo_attendance = bool(payload.get("demo_requested", payload.get("demo_attendance", raw.get("demo_attendance", False))))
    return_visit    = bool(payload.get("return_visit", raw.get("return_visit", False)))
    collateral      = payload.get("collateral", payload.get("collateral_requested", raw.get("collateral_requested", "none")))

    # Build enriched visitor for rescoring using full 41-signal framework
    # Start from pre-event enriched raw_data, then overlay on-site signals
    pre_event_raw = contact.get("raw_data") or {}

    # Map conversation quality (1-5) to 0-1
    conv_quality_score = conv_quality / 5.0

    # Extract AI analysis signals if available
    ai_intent_level  = payload.get("ai_intent_level", None)   # "strong"|"moderate"|"weak"|null
    ai_buying_signals = payload.get("ai_buying_signals", [])  # list of signal strings
    ai_score_delta   = payload.get("ai_score_delta", None)    # "+5"|"-3" etc

    # Map AI intent level to a score boost
    ai_intent_score = (
        1.0 if ai_intent_level == "strong" else
        0.6 if ai_intent_level == "moderate" else
        0.2 if ai_intent_level == "weak" else
        None  # not available
    )

    # Count high-value buying signals from AI
    high_value_signals = ["pricing", "budget", "implementation", "timeline", "authority", "decision", "proposal", "contract", "procurement"]
    ai_signal_count = sum(1 for s in ai_buying_signals if any(k in s.lower() for k in high_value_signals)) if ai_buying_signals else 0

    # Map question types to score
    qt = questions_type if isinstance(questions_type, str) else (questions_type[0] if questions_type else "general")
    questions_type_score = (
        1.0 if qt in ["pricing", "roi", "implementation"] else
        0.7 if qt in ["technical", "comparison"] else
        0.5 if qt in ["case_study", "timeline"] else
        0.3
    )

    # Map urgency to buying_cycle_stage
    urgency_val = payload.get("urgency", "")
    buying_cycle = 1.0 if urgency_val == "high" else 0.6 if urgency_val == "medium" else 0.2

    # Map collateral
    coll_val = payload.get("collateral", payload.get("collateral_requested", ""))
    collateral_specificity = (
        1.0 if coll_val in ["Pricing sheet", "Technical spec"] else
        0.6 if coll_val in ["Case study", "Brochure"] else
        0.0
    )

    # meeting booked → proposal_demo_request
    meeting_booked = bool(payload.get("meeting_booked", False))
    buying_group   = bool(payload.get("buying_group", False))

    enriched = {
        # ── Firmographic signals — these don't change on-site, preserve as-is ──
        "job_title":                contact.get("designation", pre_event_raw.get("job_title", "")),
        "icp_fit_score":            pre_event_raw.get("icp_fit_score", 0.5),
        "seniority_score":          pre_event_raw.get("seniority_score", 0.3),
        "profile_completeness":     pre_event_raw.get("profile_completeness", 0.5),
        "company_size_match":       pre_event_raw.get("company_size_match", 0.5),
        "categories_specificity":   pre_event_raw.get("categories_specificity", 0.3),
        "tech_stack_compatibility": pre_event_raw.get("tech_stack_compatibility", 0.0),
        "previous_event_history":   pre_event_raw.get("previous_event_history", 0.0),
        "trigger_event_score":      pre_event_raw.get("trigger_event_score", 0.0),

        # ── Pre-event registration signals — discount heavily (onsite overrides) ──
        "reg_timing_days":          pre_event_raw.get("reg_timing_days", 0) * 0.3,
        "email_open_rate":          pre_event_raw.get("email_open_rate", 0.0) * 0.2,
        "email_click_rate":         pre_event_raw.get("email_click_rate", 0.0) * 0.2,
        "microsite_visits":         pre_event_raw.get("microsite_visits", 0) * 0.2,
        "app_session_count":        pre_event_raw.get("app_session_count", 0) * 0.2,
        "content_downloads":        pre_event_raw.get("content_downloads", 0) * 0.2,
        "session_reg_count":        pre_event_raw.get("session_reg_count", 0) * 0.2,
        "social_mentions":          pre_event_raw.get("social_mentions", 0) * 0.2,
        "pre_event_content_eng":    pre_event_raw.get("pre_event_content_eng", 0.0) * 0.2,
        "meeting_requests_received":pre_event_raw.get("meeting_requests_received", 0) * 0.2,
        "meeting_acceptance_rate":  pre_event_raw.get("meeting_acceptance_rate", 0.0) * 0.2,
        "meeting_no_show_rate":     pre_event_raw.get("meeting_no_show_rate", 0.0),
        "private_room_bookings":    pre_event_raw.get("private_room_bookings", 0) * 0.2,
        "matchmaking_engagement":   pre_event_raw.get("matchmaking_engagement", 0.0) * 0.2,

        # ── Post-event signals — zero out (not applicable during event) ──
        "followup_response_hrs":    0.0,
        "post_event_content_eng":   0.0,
        "website_visit_post":       0.0,
        "internal_content_share":   0.0,
        "roi_report_published":     0.0,
        "social_amplification":     0.0,
        "crm_stage_progression":    0.0,

        # ── ON-SITE SIGNALS — full weight, these are ground truth ──────────────
        # If AI analysis available, blend with staff rating for conv quality
        "conv_quality_score":       (conv_quality_score * 0.6 + ai_intent_score * 0.4)
                                    if ai_intent_score is not None
                                    else conv_quality_score,
        # Boost questions score if AI detected high-value buying signals
        "questions_type_score":     min(1.0, questions_type_score + (ai_signal_count * 0.1)),
        "demo_attendance":          1.0 if demo_attendance else 0.0,
        "return_visits":            1.0 if return_visit else 0.0,
        "badge_scan_count":         1.0 if bool(payload.get("badge_scan", False)) else 0.0,
        "collateral_specificity":   collateral_specificity,    # 0-1 from collateral type
        "buying_cycle_stage":       buying_cycle,              # 0-1 from urgency
        "proposal_demo_request":    1.0 if meeting_booked else 0.0,
        "meeting_requests_sent":    1.0 if meeting_booked else 0.0,
        "competitive_displacement": 1.0 if buying_group else 0.0,
        "booth_dwell_time_min":     0.0,  # not captured yet
        "session_attend_ratio":     0.0,  # not captured yet
    }

    # Rescore using full 41-signal XGBoost model on Modal
    pre_event_iei = float(contact.get("iei_score") or 50)
    onsite_score = pre_event_iei  # fallback if Modal unavailable
    onsite_tier  = contact.get("iei_tier") or "Cool"

    if MODAL_SCORER_URL:
        try:
            async with httpx.AsyncClient(timeout=15.0) as modal_client:
                modal_resp = await modal_client.post(
                    MODAL_SCORER_URL,
                    json={"visitors": [enriched]}
                )
                if modal_resp.status_code == 200:
                    modal_data = modal_resp.json()
                    scores = modal_data.get("scores", [])
                    if scores:
                        xgb_score = float(scores[0].get("ieiScore", pre_event_iei))

                        # Blend XGBoost with onsite quality signal
                        # conv_quality_score is 0-1 (from 1-5 staff rating)
                        # At conv_quality=0.0 (score 1): onsite_weight=0.8 → XGB pulled toward 0
                        # At conv_quality=1.0 (score 5): onsite_weight=0.8 → XGB pulled toward 100
                        # Firmographic floor: even terrible engagement keeps 20% of XGBoost
                        onsite_quality_score = conv_quality_score * 100  # 0-100
                        onsite_weight = 0.7   # onsite signals dominate
                        xgb_weight    = 0.3   # firmographic baseline

                        # Bonus for strong signals
                        signal_bonus = (
                            (4 if meeting_booked else 0) +
                            (3 if return_visit else 0) +
                            (2 if demo_attendance else 0) +
                            (1 if bool(payload.get("badge_scan", False)) else 0) +
                            (buying_cycle - 0.5) * 4
                        )
                        signal_bonus = max(-5, min(signal_bonus, 8))  # hard cap -5 to +8

                        # Add AI score delta if available
                        if ai_score_delta:
                            try:
                                signal_bonus += float(str(ai_score_delta).replace("+","")) * 0.5
                            except: pass
                        signal_bonus = max(-5, min(signal_bonus, 8))

                        onsite_score = (xgb_score * xgb_weight) + (onsite_quality_score * onsite_weight) + signal_bonus

                        # ICP fit as multiplier (0.5–1.0) — non-ICP visitors can't hit top scores
                        icp_fit = float(enriched.get("icp_fit_score", 0.5))
                        icp_multiplier = 0.5 + (icp_fit * 0.5)
                        onsite_score = onsite_score * icp_multiplier

                        # Score ceiling scales with key signals fired — need ALL signals for 90+
                        key_signals_count = sum([
                            1 if conv_quality >= 4 else 0,
                            1 if meeting_booked else 0,
                            1 if return_visit else 0,
                            1 if demo_attendance else 0,
                            1 if buying_cycle >= 0.8 else 0,
                        ])
                        max_score = 55 + (key_signals_count * 9)  # 55 to 100, needs all 5 for 100
                        onsite_score = max(0.0, min(float(max_score), onsite_score))
                        onsite_tier  = "Hot" if onsite_score >= 75 else "Warm" if onsite_score >= 50 else "Cool" if onsite_score >= 25 else "Cold"
        except Exception as modal_err:
            onsite_score = onsite_adjust(pre_event_iei, enriched)
            onsite_tier  = "Hot" if onsite_score >= 75 else "Warm" if onsite_score >= 50 else "Cool" if onsite_score >= 25 else "Cold"
    else:
        onsite_score = onsite_adjust(pre_event_iei, enriched)
        onsite_tier  = "Hot" if onsite_score >= 75 else "Warm" if onsite_score >= 50 else "Cool" if onsite_score >= 25 else "Cold"

    # Save onsite signals separately
    onsite_signals = {
        "conv_quality":         payload.get("conv_quality"),
        "questions_type":       payload.get("questions_type"),
        "demo_attendance":      payload.get("demo_attendance"),
        "return_visit":         payload.get("return_visit"),
        "collateral_requested": payload.get("collateral_requested"),
        "badge_scan":           payload.get("badge_scan"),
        "buying_group":         payload.get("buying_group"),
        "meeting_booked":       payload.get("meeting_booked"),
        "meeting_completed":    payload.get("meeting_completed"),
        "urgency":              payload.get("urgency"),
        "notes":                payload.get("notes"),
    }

    # Update contact — keep pre-event iei_score, add onsite scores separately
    try:
        supabase.table("audience_contacts").update({
            "raw_data":         raw,
            "onsite_iei_score": float(onsite_score),
            "onsite_iei_tier":  onsite_tier,
        }).eq("id", contact["id"]).execute()
    except Exception as e:
        raise HTTPException(500, f"DB update failed: {str(e)}")

    # Insert into conversation_signals for duplicate detection
    try:
        supabase.table("conversation_signals").insert({
            "contact_id":           contact["id"],
            "event_id":             event_id,
            "staff_name":           payload.get("staff_name", "Staff"),
            "staff_email":          payload.get("staff_email", ""),
            "conversation_quality": int(payload.get("conversation_quality") or payload.get("conv_quality") or 0),
            "question_types":       payload.get("question_types", []),
            "return_visit":         bool(payload.get("return_visit", False)),
            "demo_requested":       bool(payload.get("demo_requested", payload.get("demo_attendance", False))),
            "badge_scan":           bool(payload.get("badge_scan", False)),
            "buying_group":         bool(payload.get("buying_group", False)),
            "meeting_booked":       bool(payload.get("meeting_booked", False)),
            "meeting_completed":    bool(payload.get("meeting_completed", False)),
            "collateral":           payload.get("collateral", payload.get("collateral_requested", "")),
            "urgency":              payload.get("urgency", ""),
            "notes":                payload.get("notes", ""),
            "ai_intent_level":      payload.get("ai_intent_level"),
            "ai_buying_signals":    payload.get("ai_buying_signals", []),
            "ai_score_delta":       payload.get("ai_score_delta"),
        }).execute()
    except Exception as e:
        print(f"[log-signal] conversation_signals insert failed: {e}")

    return {
        "ok":               True,
        "iei_score":        contact.get("iei_score", 50),   # pre-event unchanged
        "iei_tier":         contact.get("iei_tier", "Cool"),
        "onsite_iei_score": onsite_score,
        "onsite_iei_tier":  onsite_tier,
        "reg_prob":         float(contact.get("reg_prob") or 0.5),
    }


@router.get("/visitors/{event_id}")
async def search_visitors(
    event_id: str,
    q: str = "",
    request: Request = None,
):
    """Search visitors by name, company, email for Staff App. No auth required — event_id scopes access."""
    supabase = get_db()
    res = supabase.table("audience_contacts").select(
        "id,name,email,company,designation,city,country,iei_score,iei_tier,reg_prob,raw_data"
    ).eq("event_id", event_id).order("iei_score", desc=True).execute()
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



# AI conversation analysis
@router.post("/ai/analyse-conversation")
async def analyse_conversation(payload: dict):
    import anthropic, os, json, re
    visitor = payload.get("visitor", {})
    conversation = payload.get("conversation", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=api_key)
    name = visitor.get("name", "Unknown")
    company = visitor.get("company", "Unknown")
    score = round(float(visitor.get("iei_score", 0)))
    tier = visitor.get("iei_tier", "unknown")
    prompt = (
        f"You are a B2B sales intelligence agent at a trade fair.\n"
        f"Visitor: {name} from {company} (IEI: {score}, {tier} tier).\n"
        f"Conversation: \"{conversation}\"\n"
        "Respond ONLY with valid JSON (no markdown):\n"
        '{"intentLevel":"strong|moderate|weak","scoreDelta":"+5","nextQuestion":"one smart follow-up question","buyingSignals":["signal1"],"missingSignals":["gap1"],"redFlags":[],"recommendedAction":"next action","followUpHook":"email opener"}'
    )
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    match = re.search(r"{[\s\S]*}", text)
    return json.loads(match.group(0)) if match else {}


# ── Check if signal already logged today ─────────────────────────────────────
@router.get("/check-signal/{contact_id}")
async def check_signal(contact_id: str):
    """Check if a signal was already logged for this contact — return full signal for pre-population."""
    supabase = get_db()
    res = supabase.table("conversation_signals").select("*").eq(
        "contact_id", contact_id
    ).order("created_at", desc=True).limit(1).execute()
    signals = res.data or []
    if signals:
        sig = signals[0]
        return {
            "exists": True,
            "signal": {
                "conversation_quality": sig.get("conversation_quality"),
                "question_types":       sig.get("question_types"),
                "return_visit":         sig.get("return_visit"),
                "demo_requested":       sig.get("demo_requested"),
                "badge_scan":           sig.get("badge_scan"),
                "buying_group":         sig.get("buying_group"),
                "meeting_booked":       sig.get("meeting_booked"),
                "collateral":           sig.get("collateral"),
                "urgency":              sig.get("urgency"),
                "notes":                sig.get("notes"),
                "staff_name":           sig.get("staff_name"),
                "created_at":           sig.get("created_at"),
                "ai_intent_level":      sig.get("ai_intent_level"),
                "ai_buying_signals":    sig.get("ai_buying_signals"),
                "ai_score_delta":       sig.get("ai_score_delta"),
            },
            "logged_by":  sig.get("staff_name", "a staff member"),
            "logged_at":  sig.get("created_at"),
            # backward compat
            "already_logged": True,
        }
    return {"exists": False, "already_logged": False}
