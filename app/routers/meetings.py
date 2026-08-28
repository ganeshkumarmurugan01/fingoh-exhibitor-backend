"""
Fingoh Meeting Requests — endpoints for match scoring, request sending, and status tracking.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user, get_user_org
from app.database import get_db
from app.routers.audience import apply_onsite_signal
from app.routers.email_config import get_email_config_for_event, render_email_html

logger = logging.getLogger("fingoh.meetings")
from pydantic import BaseModel
from typing import Optional, List
import os, httpx, secrets, datetime, json
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

router = APIRouter(prefix="/meetings", tags=["meetings"])

MEETING_SCORER_URL = os.getenv("MEETING_SCORER_URL")
MEETING_SCORER_URLS = {
    "pharma":      os.getenv("MODAL_MEETING_SCORER_URL_PHARMA"),
    "electronics": os.getenv("MODAL_MEETING_SCORER_URL_ELECTRONICS"),
    "logistics":   os.getenv("MODAL_MEETING_SCORER_URL_LOGISTICS"),
    "general":     os.getenv("MEETING_SCORER_URL"),
}
ZOHO_CLIENT_ID     = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ZOHO_FROM_EMAIL    = os.getenv("ZOHO_FROM_EMAIL", "noreply@fingoh.ai")
ZOHO_FROM_NAME     = os.getenv("ZOHO_FROM_NAME", "Fingoh")
ZOHO_ACCOUNT_ID    = os.getenv("ZOHO_ACCOUNT_ID", "670863000000008002")
FRONTEND_URL       = os.getenv("FRONTEND_URL", "https://fingoh-exhibitor.vercel.app")
# TEST MODE: if set, all emails go to this address instead of the real contact email
TEST_EMAIL_OVERRIDE = os.getenv("TEST_EMAIL_OVERRIDE", "")


class MeetingCreate(BaseModel):
    event_id: str
    contact_id: str
    proposed_datetime: str        # ISO string
    duration_minutes: int = 30
    location: Optional[str] = None
    topic: Optional[str] = None
    notes: Optional[str] = None
    requested_by_name: Optional[str] = None
    requested_by_email: Optional[str] = None


class AIAnalysis(BaseModel):
    intentLevel: Optional[str] = None
    scoreDelta: Optional[str] = None
    recommendedAction: Optional[str] = None
    buyingSignals: Optional[List[str]] = None
    redFlags: Optional[List[str]] = None
    followUpHook: Optional[str] = None


class MeetingComplete(BaseModel):
    staff_completion_notes: Optional[str] = None
    actual_start_time: Optional[str] = None   # ISO string, staff "Start Now" tap
    actual_end_time: Optional[str] = None     # ISO string, staff "End Now" tap
    ai_analysis: Optional[AIAnalysis] = None  # structured, for historic analysis + model retraining
    staff_name: Optional[str] = None          # who completed it, for the onsite signal
    staff_email: Optional[str] = None
    voice_transcript: Optional[str] = None    # raw voice transcript, stored separately for review


async def get_zoho_access_token() -> str:
    """Get fresh Zoho access token using refresh token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://accounts.zoho.com/oauth/v2/token",
            params={
                "refresh_token": ZOHO_REFRESH_TOKEN,
                "client_id":     ZOHO_CLIENT_ID,
                "client_secret": ZOHO_CLIENT_SECRET,
                "grant_type":    "refresh_token",
            }
        )
        data = resp.json()
        if "access_token" not in data:
            raise HTTPException(500, f"Zoho auth failed: {data}")
        return data["access_token"]


async def send_meeting_email(
    to_email: str, to_name: str,
    meeting_id: str, accept_token: str, decline_token: str,
    meeting_details: dict, exhibitor_company: str,
    is_reschedule: bool = False,
):
    """Send branded meeting request email via Zoho Mail API."""
    db = get_db()
    event_id = meeting_details.get("event_id")
    cfg = get_email_config_for_event(db, event_id) if event_id else {}
    accept_url  = f"{FRONTEND_URL}/meeting?token={accept_token}&action=accept"
    decline_url = f"{FRONTEND_URL}/meeting?token={decline_token}&action=decline"

    dt = meeting_details.get("proposed_datetime", "")
    try:
        dt_fmt = datetime.datetime.fromisoformat(dt.replace("Z","")).strftime("%A, %d %B %Y at %I:%M %p")
    except:
        dt_fmt = dt

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
      <div style="background: #0D1B3E; padding: 24px; border-radius: 8px 8px 0 0;">
       <h1 style="color: white; margin: 0; font-size: 22px;">{'&#8635; Meeting Rescheduled' if is_reschedule else 'Meeting Request'}</h1>
        <p style="color: rgba(255,255,255,0.7); margin: 4px 0 0 0; font-size: 14px;">from {exhibitor_company}</p>
      </div>
      <div style="background: white; padding: 24px; border: 1px solid #E2E8F0;">
        <p style="font-size: 16px; color: #1E293B;">Dear {to_name},</p>
       <p style="color: #475569;">{'The meeting details have been updated. Please review the new time below.' if is_reschedule else f'{exhibitor_company} would like to schedule a meeting with you at the event.'}</p>
        
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 16px; margin: 20px 0;">
          <p style="margin: 0 0 8px 0; font-weight: 600; color: #1E293B;">Meeting Details</p>
          <p style="margin: 4px 0; color: #475569;"><strong>Date & Time:</strong> {dt_fmt}</p>
          <p style="margin: 4px 0; color: #475569;"><strong>Duration:</strong> {meeting_details.get('duration_minutes', 30)} minutes</p>
          {"<p style='margin: 4px 0; color: #475569;'><strong>Location:</strong> " + meeting_details.get('location','') + "</p>" if meeting_details.get('location') else ""}
          {"<p style='margin: 4px 0; color: #475569;'><strong>Topic:</strong> " + meeting_details.get('topic','') + "</p>" if meeting_details.get('topic') else ""}
          {"<p style='margin: 4px 0; color: #475569;'><strong>Notes:</strong> " + meeting_details.get('notes','') + "</p>" if meeting_details.get('notes') else ""}
        </div>

        <div style="display: flex; gap: 12px; margin: 24px 0;">
          <a href="{accept_url}" style="background: #16A34A; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px;">✓ Accept Meeting</a>
          <a href="{decline_url}" style="background: white; color: #DC2626; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1.5px solid #DC2626;">✗ Decline</a>
        </div>
        
        <p style="color: #94A3B8; font-size: 12px;">These links expire in 7 days. If you have questions, reply to this email.</p>
      </div>
      <div style="background: #F8FAFC; padding: 12px 24px; border-radius: 0 0 8px 8px; border: 1px solid #E2E8F0; border-top: none;">
        <p style="margin: 0; color: #94A3B8; font-size: 11px;">Powered by Fingoh · AI-powered trade fair intelligence</p>
      </div>
    </div>
    """

    # Wrap in branded template if config exists
    if cfg:
        html_body = render_email_html(
            body_html=html_body, config=cfg,
            visitor_name=to_name,
            event_name=meeting_details.get("event_name",""),
        )

    try:
        access_token = await get_zoho_access_token()
        async with httpx.AsyncClient() as client:
            account_id = ZOHO_ACCOUNT_ID or "670863000000008002"
            logger.info("Sending to %s from %s via account %s", to_email, ZOHO_FROM_EMAIL, account_id)
            resp = await client.post(
                f"https://mail.zoho.com/api/accounts/{account_id}/messages",
                headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
                json={
                    "fromAddress": ZOHO_FROM_EMAIL,
                    "toAddress":   to_email,
                    "subject": f"{'Meeting Rescheduled' if is_reschedule else 'Meeting Request'} from {exhibitor_company}",
                    "content":     html_body,
                    "mailFormat":  "html",
                }
            )
            logger.info("Email response: %s %s", resp.status_code, resp.text[:300])
            return resp.status_code == 200
    except Exception as e:
        logger.error("Email send failed: %s", e)
        return False



@router.get("/respond/{token}")
async def respond_to_meeting(token: str, action: str = None):
    """Public endpoint — visitor clicks Accept/Decline link in email."""
    db = get_db()

    # Find token
    token_res = db.table("meeting_tokens").select("*").eq("token", token).maybe_single().execute()
    if not token_res or not token_res.data:
        raise HTTPException(404, "Invalid or expired link")

    token_row = token_res.data
    if token_row["used"]:
        return {"status": "already_responded", "action": token_row["action"]}

    # Check expiry
    expires = datetime.datetime.fromisoformat(token_row["expires_at"].replace("Z","+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    if now > expires:
        raise HTTPException(410, "This link has expired")

    # Determine action from token or query param
    final_action = token_row["action"] if not action else action

    # Update meeting status
    new_status = "accepted" if final_action == "accept" else "declined"
    db.table("meeting_requests").update({
        "status": new_status,
        "responded_at": datetime.datetime.utcnow().isoformat(),
    }).eq("id", token_row["meeting_id"]).execute()

    # Mark token as used
    db.table("meeting_tokens").update({"used": True}).eq("id", token_row["id"]).execute()

    # Get meeting details for response page
    meeting_res = db.table("meeting_requests").select(
        "*, audience_contacts(name, email, company)"
    ).eq("id", token_row["meeting_id"]).maybe_single().execute()
    meeting = meeting_res.data if meeting_res and meeting_res.data else {}

    return {
        "status": new_status,
        "action": final_action,
        "meeting": meeting,
    }

@router.get("/staff/{event_id}")
async def list_meetings_for_staff(event_id: str):
    """
    Public — no org JWT. Staff App Meetings tab calls this.
    Returns accepted + completed meetings so completed cards stay visible.
    """
    db = get_db()
    res = db.table("meeting_requests").select(
        "*, audience_contacts(name, email, designation, company, phone)"
    ).eq("event_id", event_id).in_("status", ["accepted", "completed"]).order("proposed_datetime").execute()
    return res.data or []


def _build_completion_update(payload: MeetingComplete) -> dict:
    """Shared by both /complete endpoints. Folds a short readable AI summary
    into staff_completion_notes while keeping the full structured analysis
    in ai_analysis (jsonb) for later retraining/aggregation."""
    update = {
        "status": "completed",
        "completed_at": datetime.datetime.utcnow().isoformat(),
        "staff_completion_notes": payload.staff_completion_notes,
        "voice_transcript": payload.voice_transcript,
    }
    if payload.actual_start_time:
        update["actual_start_time"] = payload.actual_start_time
    if payload.actual_end_time:
        update["actual_end_time"] = payload.actual_end_time
    if payload.ai_analysis:
        a = payload.ai_analysis
        update["ai_analysis"] = a.dict()
        summary_bits = []
        if a.intentLevel: summary_bits.append(f"Intent: {a.intentLevel}")
        if a.recommendedAction: summary_bits.append(f"Next: {a.recommendedAction}")
        if a.followUpHook: summary_bits.append(f"Follow-up: {a.followUpHook}")
        if summary_bits:
            summary = " · ".join(summary_bits)
            base_notes = (payload.staff_completion_notes or "").strip()
            update["staff_completion_notes"] = f"{base_notes}\n\n[AI] {summary}".strip()
    return update


def _meeting_signal_payload(payload: MeetingComplete) -> dict:
    """
    A completed meeting is itself a strong onsite signal — build a payload
    apply_onsite_signal() can score, even when the visitor never had a
    separate logged conversation.
    conv_quality defaults to a solid baseline (a scheduled 1:1 happened) and
    is nudged by the AI intent read, if one was captured.
    """
    conv_quality = 4  # baseline: completed scheduled meeting, out of 5
    ai_intent_level = None
    ai_buying_signals = []
    ai_score_delta = None
    if payload.ai_analysis:
        a = payload.ai_analysis
        ai_intent_level = a.intentLevel
        ai_buying_signals = a.buyingSignals or []
        ai_score_delta = a.scoreDelta
        conv_quality = {"strong": 5, "moderate": 4, "weak": 2}.get(a.intentLevel, 4)

    return {
        "meeting_booked": True,
        "meeting_completed": True,
        "conv_quality": conv_quality,
        "conversation_quality": conv_quality,
        "notes": payload.staff_completion_notes or "",
        "staff_name": payload.staff_name or "Staff",
        "staff_email": payload.staff_email or "",
        "ai_intent_level": ai_intent_level,
        "ai_buying_signals": ai_buying_signals,
        "ai_score_delta": ai_score_delta,
    }


async def _log_meeting_completion_signal(db, meeting_id: str, payload: MeetingComplete):
    """Fetch the meeting + contact and feed the completion into the shared
    onsite scoring pipeline. Best-effort — a scoring hiccup shouldn't block
    the meeting from being marked complete."""
    try:
        meeting_res = db.table("meeting_requests").select("event_id, contact_id").eq("id", meeting_id).maybe_single().execute()
        if not meeting_res or not meeting_res.data:
            return
        event_id, contact_id = meeting_res.data["event_id"], meeting_res.data["contact_id"]
        contact_res = db.table("audience_contacts").select("*").eq("id", contact_id).maybe_single().execute()
        if not contact_res or not contact_res.data:
            return
        await apply_onsite_signal(db, event_id, contact_res.data, _meeting_signal_payload(payload))
    except Exception as e:
        logger.error("Onsite signal logging failed: %s", e)


@router.patch("/staff/{meeting_id}/complete")
async def complete_meeting_staff(meeting_id: str, payload: MeetingComplete):
    """Public — Staff App marks a meeting completed + adds notes on the floor."""
    db = get_db()
    db.table("meeting_requests").update(_build_completion_update(payload)).eq("id", meeting_id).execute()
    await _log_meeting_completion_signal(db, meeting_id, payload)
    return {"status": "completed"}


@router.get("/{event_id}")
async def list_meetings(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all meetings for an event with contact details."""
    db = get_db()
    res = db.table("meeting_requests").select(
        "*, audience_contacts(name, email, designation, company, country, iei_score, iei_tier, reg_prob, raw_data)"
    ).eq("event_id", event_id).order("match_score", desc=True).execute()
    return res.data or []


@router.get("/{event_id}/prospects")
async def get_meeting_prospects(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get ranked prospects for meeting requests using IEI-based match scoring."""
    db = get_db()

    # Get all contacts for this event
    contacts_res = db.table("audience_contacts").select("*").eq("event_id", event_id).execute()
    contacts = contacts_res.data or []

    if not contacts:
        return []

    # Get event details for industry vertical routing
    event_res = db.table("events").select("name, company, industry_vertical").eq("id", event_id).maybe_single().execute()
    event = event_res.data if event_res and event_res.data else {}

    # Get existing meeting requests to mark already-requested contacts
    meetings_res = db.table("meeting_requests").select(
        "id, contact_id, status, proposed_datetime, completed_at, actual_start_time, actual_end_time, duration_minutes, location, topic, staff_completion_notes, ai_analysis"
    ).eq("event_id", event_id).execute()
    requested = {}
    meeting_details = {}
    for m in (meetings_res.data or []):
        cid = m["contact_id"]
        # Keep the most recent / highest-priority meeting per contact
        existing = requested.get(cid)
        priority = {"accepted": 4, "pending": 3, "completed": 2, "declined": 1, "cancelled": 0}
        if existing is None or priority.get(m["status"], 0) > priority.get(existing, 0):
            requested[cid] = m["status"]
            meeting_details[cid] = m

    # ── Fetch exhibitor context for bilateral scoring ────────────────────────
    icp_res    = db.table("event_icp").select("*").eq("event_id", event_id).maybe_single().execute()
    intent_res = db.table("event_intent").select("*").eq("event_id", event_id).maybe_single().execute()
    ex_icp    = icp_res.data if icp_res and icp_res.data else {}
    ex_intent = intent_res.data if intent_res and intent_res.data else {}

    ex_roles        = [r.lower() for r in (ex_icp.get("roles") or [])]
    ex_sizes        = [s.lower() for s in (ex_icp.get("company_sizes") or [])]
    ex_visit_reasons= [r.lower() for r in (ex_icp.get("visit_reasons") or [])]
    ex_intent_why   = (ex_intent.get("why") or "").lower()
    ex_intent_buyers= (ex_intent.get("buyers") or "").lower()

    def _compute_match_score(c: dict) -> tuple[float, float]:
        """
        Compute bilateral match score using 5 dimensions from Fingoh Match architecture.
        Returns (match_score 0-100, meeting_prob 0-1)
        """
        raw   = c.get("raw_data") or {}
        title = (c.get("designation") or raw.get("designation") or raw.get("Designation") or "").lower()
        reason_raw = (c.get("primary_reason") or raw.get("primary_reason") or raw.get("Primary Reason") or "").lower()
        # Handle pipe-separated reasons
        reason = reason_raw.replace("|", " ").lower()
        cats_raw = (c.get("categories_interest") or raw.get("categories_interest") or raw.get("Categories of Interest") or "").lower()
        company_size = (c.get("company_size") or raw.get("company_size") or raw.get("Company Size") or "").lower()
        company = (c.get("company") or raw.get("company") or raw.get("Company") or "").lower()
        timeline_raw = (raw.get("visit_timeline") or raw.get("Visit Timeline") or raw.get("purchase_timeline") or "").lower()
        company_type = (raw.get("company_type") or raw.get("Company Type") or "").lower()

        # meeting_interest — handle string "yes"/"no" and boolean
        mi_raw = raw.get("meeting_interest") or raw.get("Meeting Interest") or c.get("meeting_interest")
        if mi_raw in (True, "yes", "Yes", "YES", "y", 1):
            meeting_interest = "yes"
        elif mi_raw in (False, "no", "No", "NO", "n", 0):
            meeting_interest = "no"
        else:
            meeting_interest = "unknown"

        iei       = float(c.get("iei_score") or 0)
        iei_tier  = (c.get("iei_tier") or "T4")
        reg_prob  = float(c.get("reg_prob") or 0.5)
        cat_match = float(raw.get("category_match_score") or c.get("category_match_score") or 0.0)
        proc_mandate = float(raw.get("procurement_mandate_score") or 0.0)
        icp_fit   = float(raw.get("icp_fit_score") or c.get("icp_fit_score") or min(iei / 100.0, 1.0))
        seniority = float(raw.get("seniority_score") or 0.0)
        if not seniority:
            if any(x in title for x in ["ceo","cto","cfo","coo","chief","president","md","managing director"]):
                seniority = 1.0
            elif any(x in title for x in ["vp","vice president","svp","evp","director","head of"]):
                seniority = 0.85
            elif any(x in title for x in ["manager","senior","lead","principal","general manager"]):
                seniority = 0.60
            elif any(x in title for x in ["engineer","analyst","executive","specialist","consultant"]):
                seniority = 0.35
            else:
                seniority = 0.30

        # ── Dimension 1: Intent Alignment (35%) ──────────────────────────────
        # Visitor's commercial intent vs exhibitor's buyer intent goals
        SOURCING_KEYWORDS = ["sourcing","procurement","vendor","supplier","purchase","buy","rfp","tender","contract","evaluating"]
        RESEARCH_KEYWORDS = ["research","learn","network","explore","knowledge","benchmark","compare"]

        has_sourcing = any(k in reason for k in SOURCING_KEYWORDS)
        has_research = any(k in reason for k in RESEARCH_KEYWORDS)

        # Base intent from reason
        if has_sourcing:
            base_intent = 0.75
        elif has_research:
            base_intent = 0.40
        else:
            base_intent = 0.30

        # Enrichment status — unenriched contacts get reduced boosts
        enrichment_status = c.get("enrichment_status", "pending")
        is_enriched = enrichment_status == "done"

        # Boost from meeting_interest — reduced for unenriched contacts
        mi_boost = 0.25 if is_enriched else 0.12
        if meeting_interest == "yes":
            base_intent = min(base_intent + mi_boost, 1.0)
        elif meeting_interest == "no":
            base_intent = max(base_intent - 0.30, 0.0)

        # Boost from procurement_mandate (Claude-computed)
        if proc_mandate > 0:
            base_intent = min(base_intent + proc_mandate * 0.20, 1.0)

        # Boost from category_match_score (semantic alignment)
        if cat_match > 0:
            base_intent = min(base_intent + cat_match * 0.15, 1.0)

        # Check alignment with exhibitor's stated intent
        ex_intent_match = 0.0
        if ex_intent_why:
            intent_keywords = ex_intent_why.split()
            matching = sum(1 for kw in intent_keywords if len(kw) > 4 and kw in reason)
            ex_intent_match = min(matching / max(len(intent_keywords), 1), 1.0) * 0.15
        base_intent = min(base_intent + ex_intent_match, 1.0)

        intent_alignment = base_intent

        # ── Dimension 2: ICP Bilateral Fit (25%) ─────────────────────────────
        # Visitor firmographic vs exhibitor ICP definition
        role_score = 0.5  # neutral default
        if ex_roles:
            ROLE_KW = {
                "c-suite / ceo / md":       ["ceo","cto","cfo","chief","president","managing director","md"],
                "vp / director":            ["vp","vice president","director","head of","svp","evp"],
                "procurement manager":      ["procurement","purchasing","sourcing","supply chain","buyer"],
                "clinical / technical lead":["clinical","technical","r&d","research","engineer"],
                "business owner":           ["owner","founder","co-founder","proprietor","entrepreneur"],
                "consultant / advisor":     ["consultant","advisor","analyst","specialist"],
                "department head":          ["head","department","division","general manager"],
            }
            best = 0.0
            for ex_role in ex_roles:
                kws = ROLE_KW.get(ex_role.lower(), [ex_role.lower()])
                if any(kw in title for kw in kws):
                    best = 1.0
                    break
                # Partial match
                ex_words = ex_role.lower().split()
                if any(w in title for w in ex_words if len(w) > 3):
                    best = max(best, 0.6)
            role_score = best if best > 0 else 0.2

        size_score = 0.5
        if ex_sizes and company_size:
            for ex_size in ex_sizes:
                es = ex_size.lower()
                cs = company_size
                if es in cs or cs in es:
                    size_score = 1.0; break
                if "1000+" in es and any(x in cs for x in ["1000","large","enterprise","mnc"]):
                    size_score = 1.0; break
                if "501" in es and any(x in cs for x in ["500","501","600","700","800","900"]):
                    size_score = 0.9; break
                if "201" in es and any(x in cs for x in ["200","201","250","300","400"]):
                    size_score = 0.9; break
                if "51" in es and any(x in cs for x in ["50","51","75","100","150"]):
                    size_score = 0.9; break

        # Use Claude icp_fit_score if available, otherwise compute from role + size
        if icp_fit > 0:
            icp_bilateral_fit = icp_fit * 0.6 + role_score * 0.25 + size_score * 0.15
        else:
            icp_bilateral_fit = role_score * 0.5 + size_score * 0.3 + seniority * 0.2

        icp_bilateral_fit = float(min(icp_bilateral_fit, 1.0))

        # ── Dimension 3: Tier Correlation (20%) ──────────────────────────────
        # T1/T2 → high commercial value; T3/T4 → suppressed
        TIER_SCORE = {"T1": 1.0, "T2": 0.75, "T3": 0.40, "T4": 0.15}
        tier_correlation = TIER_SCORE.get(iei_tier, 0.30)

        # Penalise unenriched contacts — IEI score unvalidated by Claude
        if not is_enriched:
            tier_correlation *= 0.5  # halve tier weight for unenriched

        # Hard cap: T4 visitors can never score above 35 overall
        t4_cap = iei_tier == "T4"
        # Additional cap for skipped/failed contacts — max 55
        unenriched_cap = enrichment_status in ("skipped", "failed")

        # ── Dimension 4: Timing Alignment (12%) ──────────────────────────────
        # Visitor buying timeline vs exhibitor's sales readiness
        if any(x in timeline_raw for x in ["immediate","urgent","asap","now","this month","q1","q2","q3","q4"]):
            timing_alignment = 1.0
        elif any(x in timeline_raw for x in ["3 month","three month","quarter","soon"]):
            timing_alignment = 0.80
        elif any(x in timeline_raw for x in ["6 month","six month","half year"]):
            timing_alignment = 0.60
        elif any(x in timeline_raw for x in ["12 month","one year","annual","next year"]):
            timing_alignment = 0.40
        elif any(x in timeline_raw for x in ["no timeline","not sure","exploring","undecided"]):
            timing_alignment = 0.25
        else:
            # Use buying_cycle_stage from Claude if available
            bcs = float(raw.get("buying_cycle_stage") or 0.0)
            timing_alignment = bcs if bcs > 0 else 0.45  # neutral if unknown

        # Boost from trigger_event_score
        trigger = float(raw.get("trigger_event_score") or 0.0)
        if trigger > 0:
            timing_alignment = min(timing_alignment + trigger * 0.15, 1.0)

        # ── Dimension 5: Prior Engagement (8%) ───────────────────────────────
        # Existing signals of relationship/research
        microsite    = min(float(raw.get("microsite_visits") or 0) / 5.0, 1.0)
        email_click  = float(raw.get("email_click_rate") or 0.0)
        content_dl   = min(float(raw.get("content_downloads") or 0) / 3.0, 1.0)
        prev_hist    = float(raw.get("previous_event_history") or 0.0)
        repeat_buyer = float(raw.get("repeat_buyer_potential") or 0.0)

        prior_engagement = (
            microsite    * 0.25 +
            email_click  * 0.20 +
            content_dl   * 0.15 +
            prev_hist    * 0.25 +
            repeat_buyer * 0.15
        )
        # Reg prob boosts prior engagement slightly
        prior_engagement = min(prior_engagement + reg_prob * 0.10, 1.0)

        # ── Final Match Score ─────────────────────────────────────────────────
        raw_score = (
            intent_alignment   * 0.35 +
            icp_bilateral_fit  * 0.25 +
            tier_correlation   * 0.20 +
            timing_alignment   * 0.12 +
            prior_engagement   * 0.08
        )

        # Hard cap for T4 visitors
        if t4_cap:
            raw_score = min(raw_score, 0.35)

        # Cap for unenriched contacts (skipped/failed enrichment)
        if unenriched_cap:
            raw_score = min(raw_score, 0.55)

        match_score = round(min(raw_score * 100, 100), 1)

        # Calibrated meeting_prob — slightly more conservative than raw score
        meeting_prob = round(min(raw_score * 0.90 + 0.05, 1.0), 4)

        return match_score, meeting_prob

    # Build response
    results = []
    for c in contacts:
        match_score, meeting_prob = _compute_match_score(c)
        raw = c.get("raw_data") or {}
        results.append({
            "contact_id":           c["id"],
            "name":                 c.get("name", c.get("email", "Unknown")),
            "designation":          c.get("designation", "—"),
            "company":              c.get("company", "—"),
            "country":              c.get("country", "—"),
            "email":                c.get("email", ""),
            "iei_score":            c.get("iei_score", 0),
            "iei_tier":             c.get("iei_tier", "T4"),
            "reg_prob":             c.get("reg_prob", 0.5),
            "cached_analysis":      c.get("meeting_match_analysis"),
            "cached_analysed_at":   c.get("meeting_match_analysed_at"),
            "primary_reason":       c.get("primary_reason") or raw.get("primary_reason") or raw.get("Primary Reason", ""),
            "categories_interest":  c.get("categories_interest") or raw.get("categories_interest") or raw.get("Categories of Interest", ""),
            "meeting_interest":     raw.get("meeting_interest") or raw.get("Meeting Interest") or c.get("meeting_interest"),
            "purchase_timeline":    raw.get("visit_timeline") or raw.get("Visit Timeline") or raw.get("purchase_timeline", ""),
            "actively_sourcing":    raw.get("actively_sourcing", False),
            "specific_product":     raw.get("specific_product_interest") or raw.get("Specific Product Interest", ""),
            "company_size":         c.get("company_size") or raw.get("company_size") or raw.get("Company Size", ""),
            "match_score":          match_score,
            "meeting_prob":         meeting_prob,
            "meeting_status":       requested.get(c["id"]),
            "meeting":              meeting_details.get(c["id"]),
            "category_match_score": float(raw.get("category_match_score") or c.get("category_match_score") or 0.0),
            "match_reasoning":      raw.get("match_reasoning") or c.get("match_reasoning") or "",
        })

    # Sort by match score descending
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results



class MatchAnalysisRequest(BaseModel):
    prospect: dict
    exhibitor: dict
    force_refresh: bool = False

@router.post("/match-analysis")
async def match_analysis(
    payload: MatchAnalysisRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generate IEI-based intent match analysis between a prospect and exhibitor ICP."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    p   = payload.prospect
    ex  = payload.exhibitor
    contact_id = p.get("contact_id")

    # ── Check cache unless force_refresh ─────────────────────────────────────
    if contact_id and not payload.force_refresh:
        db = get_db()
        cached = db.table("audience_contacts").select(
            "meeting_match_analysis, meeting_match_analysed_at"
        ).eq("id", contact_id).maybe_single().execute()
        if cached and cached.data and cached.data.get("meeting_match_analysis"):
            result = cached.data["meeting_match_analysis"]
            result["cached_at"] = cached.data.get("meeting_match_analysed_at")
            result["from_cache"] = True
            return result

    icp_roles   = ", ".join(ex.get("icpRole")   or []) or "Not specified"
    icp_sizes   = ", ".join(ex.get("icpSize")   or []) or "Not specified"
    icp_reasons = ", ".join(ex.get("icpReason") or []) or "Not specified"

    prompt = f"""You are an expert B2B event intelligence system analysing meeting match quality at a trade fair.

EXHIBITOR PROFILE:
- Company: {ex.get("company", "Unknown")}
- Event: {ex.get("name", "Unknown")}
- Target buyer roles: {icp_roles}
- Target company sizes: {icp_sizes}
- Visitor intent they want to attract: {icp_reasons}

VISITOR PROFILE:
- Name: {p.get("name")}
- Role: {p.get("designation")}
- Company: {p.get("company")} ({p.get("country")})
- IEI Score: {float(p.get("iei_score") or 0):.1f} ({p.get("iei_tier", "T3")} tier)
- Visit reason: {p.get("primary_reason") or "Not stated"}
- Product categories of interest: {p.get("categories_interest") or "Not specified"}
- Wants meeting: {"YES - explicitly opted in" if p.get("meeting_interest") in [True, "yes"] else "NO - opted out" if p.get("meeting_interest") in [False, "no"] else "Not specified"}
- Purchase timeline: {p.get("purchase_timeline") or "Not stated"}
- Actively sourcing: {"Yes" if p.get("actively_sourcing") else "No"}
- Specific product interest: {p.get("specific_product") or "Not stated"}
- IEI match score: {round(float(p.get("match_score") or 0))}/100
- Meeting probability: {round(float(p.get("meeting_prob") or 0)*100)}%

Analyse whether this visitor is genuinely a good meeting candidate for this exhibitor. Consider:
1. Does their ROLE match the exhibitor's target buyer roles?
2. Does their INTENT (visit reason, categories, sourcing status) align with what the exhibitor offers?
3. Are there RED FLAGS (e.g. wrong department, policy vs procurement, research only, competitor)?
4. What is the REAL probability of a productive meeting given the intent signals?

Return ONLY valid JSON (no markdown, no explanation outside JSON):
{{
  "intentAlignment": "HIGH or MED or LOW",
  "alignmentSummary": "2-sentence honest assessment of fit between this visitor and exhibitor",
  "matchFactors": [
    {{"factor": "string", "assessment": "string", "impact": "POSITIVE or NEUTRAL or NEGATIVE"}}
  ],
  "redFlags": ["string"],
  "talkingPoints": ["string"],
  "recommendation": "Priority meeting or Worth exploring or Low priority",
  "recommendationReason": "1-sentence honest recommendation with specific reasoning"
}}"""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )

        if resp.status_code != 200:
            raise HTTPException(502, f"Claude API error {resp.status_code}: {resp.text[:500]}")

        raw = resp.json()["content"][0]["text"]
        # Strip markdown fences if any
        clean = raw.strip()
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        analysis = json.loads(clean.strip())
        analysis["from_cache"] = False

        # ── Save to DB ────────────────────────────────────────────────────────
        if contact_id:
            try:
                db = get_db()
                db.table("audience_contacts").update({
                    "meeting_match_analysis":    analysis,
                    "meeting_match_analysed_at": datetime.datetime.utcnow().isoformat(),
                }).eq("id", contact_id).execute()
            except Exception as save_err:
                logger.warning("Cache save error: %s", save_err)

        return analysis

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"match_analysis error: {type(e).__name__}: {str(e)}")


@router.post("")
async def create_meeting_request(
    payload: MeetingCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a meeting request and send email to visitor."""
    db = get_db()
    org_id = get_user_org(current_user["user_id"], db)

    # Get contact details
    contact_res = db.table("audience_contacts").select("*").eq("id", payload.contact_id).maybe_single().execute()
    if not contact_res or not contact_res.data:
        raise HTTPException(404, "Contact not found")
    contact = contact_res.data

    # Get event details for company name
    event_res = db.table("events").select("name, company, industry_vertical").eq("id", payload.event_id).maybe_single().execute()
    event = event_res.data if event_res and event_res.data else {}

    # Check no duplicate pending meeting
    existing = db.table("meeting_requests").select("id, status").eq("event_id", payload.event_id).eq("contact_id", payload.contact_id).execute()
    if existing.data:
        active = [m for m in existing.data if m["status"] in ("pending", "accepted")]
        if active:
            raise HTTPException(409, "A meeting request already exists for this contact")

    # Create meeting record
    meeting_row = {
        "event_id":           payload.event_id,
        "contact_id":         payload.contact_id,
        "org_id":             org_id,
        "proposed_datetime":  payload.proposed_datetime,
        "duration_minutes":   payload.duration_minutes,
        "location":           payload.location,
        "topic":              payload.topic,
        "notes":              payload.notes,
        "requested_by_name":  payload.requested_by_name,
        "requested_by_email": payload.requested_by_email,
        "status":             "pending",
    }
    meeting_res = db.table("meeting_requests").insert(meeting_row).execute()
    if not meeting_res.data:
        raise HTTPException(500, "Failed to create meeting request")
    meeting = meeting_res.data[0]
    meeting_id = meeting["id"]

    # Create accept/decline tokens
    accept_token  = secrets.token_hex(32)
    decline_token = secrets.token_hex(32)

    db.table("meeting_tokens").insert([
        {"meeting_id": meeting_id, "token": accept_token,  "action": "accept"},
        {"meeting_id": meeting_id, "token": decline_token, "action": "decline"},
    ]).execute()

    # Send email
    email_sent = False
    contact_email = TEST_EMAIL_OVERRIDE if TEST_EMAIL_OVERRIDE else contact.get("email", "")
    if contact_email:
        email_sent = await send_meeting_email(
            to_email=contact_email,
            to_name=contact.get("name", contact["email"]),
            meeting_id=meeting_id,
            accept_token=accept_token,
            decline_token=decline_token,
            meeting_details={
                "proposed_datetime": payload.proposed_datetime,
                "duration_minutes":  payload.duration_minutes,
                "location":          payload.location,
                "topic":             payload.topic,
                "notes":             payload.notes,
            },
            exhibitor_company=event.get("company", "The exhibitor"),
        )

    return {**meeting, "email_sent": email_sent}



@router.patch("/{meeting_id}/complete")
async def complete_meeting(
    meeting_id: str,
    payload: MeetingComplete,
    current_user: dict = Depends(get_current_user),
):
    """Mark a meeting as completed (called from Staff App)."""
    db = get_db()
    db.table("meeting_requests").update(_build_completion_update(payload)).eq("id", meeting_id).execute()
    await _log_meeting_completion_signal(db, meeting_id, payload)
    return {"status": "completed"}


@router.patch("/{meeting_id}/cancel")
async def cancel_meeting(
    meeting_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Cancel a pending meeting request."""
    db = get_db()
    db.table("meeting_requests").update({"status": "cancelled"}).eq("id", meeting_id).execute()
    return {"status": "cancelled"}


class MeetingReschedule(BaseModel):
    proposed_datetime: str
    duration_minutes: int = 30
    location: Optional[str] = None
    topic: Optional[str] = None
    notes: Optional[str] = None


@router.patch("/{meeting_id}/reschedule")
async def reschedule_meeting(
    meeting_id: str,
    payload: MeetingReschedule,
    current_user: dict = Depends(get_current_user),
):
    """Reschedule a meeting — update datetime, reset to pending, resend email."""
    db = get_db()

    # Get existing meeting
    meeting_res = db.table("meeting_requests").select("*").eq("id", meeting_id).maybe_single().execute()
    if not meeting_res or not meeting_res.data:
        raise HTTPException(404, "Meeting not found")
    meeting = meeting_res.data

    # Get contact details
    contact_res = db.table("audience_contacts").select("*").eq("id", meeting["contact_id"]).maybe_single().execute()
    contact = contact_res.data if contact_res and contact_res.data else {}

    # Get event details
    event_res = db.table("events").select("name, company, industry_vertical").eq("id", meeting["event_id"]).maybe_single().execute()
    event = event_res.data if event_res and event_res.data else {}

    # Update meeting record
    db.table("meeting_requests").update({
        "proposed_datetime": payload.proposed_datetime,
        "duration_minutes":  payload.duration_minutes,
        "location":          payload.location,
        "topic":             payload.topic,
        "notes":             payload.notes,
        "status":            "pending",
        "completed_at":      None,
    }).eq("id", meeting_id).execute()

    # Create new accept/decline tokens
    accept_token  = secrets.token_hex(32)
    decline_token = secrets.token_hex(32)

    db.table("meeting_tokens").insert([
        {"meeting_id": meeting_id, "token": accept_token,  "action": "accept"},
        {"meeting_id": meeting_id, "token": decline_token, "action": "decline"},
    ]).execute()

    # Resend email
    email_sent = False
    contact_email = TEST_EMAIL_OVERRIDE if TEST_EMAIL_OVERRIDE else contact.get("email", "")
    if contact_email:
        email_sent = await send_meeting_email(
            to_email=contact_email,
            to_name=contact.get("name", contact.get("email", "")),
            meeting_id=meeting_id,
            accept_token=accept_token,
            decline_token=decline_token,
            meeting_details={
                "proposed_datetime": payload.proposed_datetime,
                "duration_minutes":  payload.duration_minutes,
                "location":          payload.location,
                "topic":             payload.topic,
                "notes":             payload.notes,
            },
            exhibitor_company=event.get("company", "The exhibitor"),
            is_reschedule=True,
        )

    return {"ok": True, "status": "pending", "email_sent": email_sent}
