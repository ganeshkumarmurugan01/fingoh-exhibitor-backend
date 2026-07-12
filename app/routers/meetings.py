"""
Fingoh Meeting Requests — endpoints for match scoring, request sending, and status tracking.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user, get_user_org
from app.database import get_db
from app.routers.audience import apply_onsite_signal
from pydantic import BaseModel
from typing import Optional, List
import os, httpx, secrets, datetime, json
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

router = APIRouter(prefix="/meetings", tags=["meetings"])

MEETING_SCORER_URL = os.getenv("MEETING_SCORER_URL")
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
    """Send meeting request email via Zoho Mail API."""
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

    try:
        access_token = await get_zoho_access_token()
        async with httpx.AsyncClient() as client:
            account_id = ZOHO_ACCOUNT_ID or "670863000000008002"
            print(f"[EMAIL] Sending to {to_email} from {ZOHO_FROM_EMAIL} via account {account_id}")
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
            print(f"[EMAIL] Response: {resp.status_code} {resp.text[:300]}")
            return resp.status_code == 200
    except Exception as e:
        print(f"[EMAIL] Send failed: {e}")
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
        print(f"[meetings] onsite signal logging failed: {e}")


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
    """Get ranked prospects for meeting requests using LambdaMART scoring."""
    db = get_db()

    # Get all contacts for this event
    contacts_res = db.table("audience_contacts").select("*").eq("event_id", event_id).execute()
    contacts = contacts_res.data or []

    if not contacts:
        return []

    # Get existing meeting requests to mark already-requested contacts
    meetings_res = db.table("meeting_requests").select("contact_id, status").eq("event_id", event_id).execute()
    requested = {m["contact_id"]: m["status"] for m in (meetings_res.data or [])}

    # Score contacts using LambdaMART
    def derive_features(c):
        """Derive LambdaMART features from available contact data."""
        raw = c.get("raw_data") or {}
        title = (c.get("designation") or "").lower()
        reason = (c.get("primary_reason") or "").lower()
        cats = c.get("categories_interest") or ""
        company_size = (c.get("company_size") or "").lower()

        # Seniority from job title
        if any(x in title for x in ["ceo","cto","cfo","chief","president","md","managing director"]):
            seniority = 1.0
        elif any(x in title for x in ["vp","vice","svp","director","head"]):
            seniority = 0.85
        elif any(x in title for x in ["manager","senior","lead","principal"]):
            seniority = 0.6
        elif any(x in title for x in ["engineer","analyst","executive","specialist"]):
            seniority = 0.35
        else:
            seniority = 0.3

        # Company size match (larger = more likely to need meetings)
        if any(x in company_size for x in ["10000","5000"]):
            size_score = 1.0
        elif any(x in company_size for x in ["2000","1000"]):
            size_score = 0.8
        elif any(x in company_size for x in ["500","200"]):
            size_score = 0.6
        elif any(x in company_size for x in ["50","100"]):
            size_score = 0.4
        else:
            size_score = 0.5

        # Buying intent from visit reason
        buying_signals = ["sourcing","procurement","vendor","supplier","evaluating","purchase","buy","rfp","tender","contract"]
        buying_score = 0.8 if any(x in reason for x in buying_signals) else 0.3

        # Category specificity — more categories = more specific interest
        cat_count = len([c for c in cats.split(",") if c.strip()]) if cats else 0
        cat_score = min(cat_count / 3.0, 1.0)

        # ICP fit from iei_score (normalized)
        iei = c.get("iei_score") or 50
        icp_fit = min(iei / 100.0, 1.0)

        # Profile completeness — how many fields are filled
        fields = [c.get("designation"), c.get("company"), c.get("country"),
                  c.get("primary_reason"), c.get("linkedin_url"), c.get("company_size")]
        completeness = sum(1 for f in fields if f) / len(fields)

        # Reg prob as proxy for attendance commitment
        reg_prob = c.get("reg_prob") or 0.5

        # Meeting interest — direct registration signal (strongest predictor)
        meeting_interest = c.get("meeting_interest")
        if meeting_interest is True:
            meeting_interest_score = 1.0
        elif meeting_interest is False:
            meeting_interest_score = 0.0
        else:
            meeting_interest_score = 0.5  # unknown

        # Boost trigger score significantly if meeting_interest is True
        trigger = buying_score * reg_prob
        if meeting_interest is True:
            trigger = min(trigger + 0.4, 1.0)
        elif meeting_interest is False:
            trigger = max(trigger - 0.3, 0.0)

        return {
            "job_title":              c.get("designation", ""),
            "iei_score":              iei,
            "icp_fit_score":          icp_fit,
            "buying_cycle_stage":     buying_score if meeting_interest is not True else min(buying_score + 0.3, 1.0),
            "microsite_visits":       raw.get("microsite_visits", 0),
            "content_downloads":      raw.get("content_downloads", 0),
            "email_click_rate":       raw.get("email_click_rate", 0),
            "meeting_requests_sent":  1.0 if meeting_interest is True else raw.get("meeting_requests_sent", 0),
            "profile_completeness":   completeness,
            "categories_specificity": cat_score,
            "reg_timing_days":        raw.get("reg_timing_days", 45),
            "trigger_event_score":    trigger,
            "competitive_displacement": raw.get("competitive_displacement", 0),
            "previous_event_history": raw.get("previous_event_history", 0),
            "company_size_match":     size_score,
            "meeting_interest":       meeting_interest_score,
        }

    visitors_payload = [derive_features(c) for c in contacts]

    # Call Modal LambdaMART scorer
    match_scores = {}
    if MEETING_SCORER_URL:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(MEETING_SCORER_URL, json={"visitors": visitors_payload})
                if resp.status_code == 200:
                    scores = resp.json().get("scores", [])
                    for i, c in enumerate(contacts):
                        if i < len(scores):
                            match_scores[c["id"]] = scores[i]
        except Exception as e:
            print(f"Meeting scorer error: {e}")

    # Build response
    results = []
    for c in contacts:
        score_data = match_scores.get(c["id"], {"matchScore": round(c.get("iei_score", 50), 1), "meetingProb": round((c.get("iei_score", 50) or 50) / 100, 3)})
        raw = c.get("raw_data") or {}
        results.append({
            "contact_id":       c["id"],
            "name":             c.get("name", c.get("email", "Unknown")),
            "designation":      c.get("designation", "—"),
            "company":          c.get("company", "—"),
            "country":          c.get("country", "—"),
            "email":            c.get("email", ""),
            "iei_score":        c.get("iei_score", 0),
            "iei_tier":         c.get("iei_tier", "Cool"),
            "reg_prob":         c.get("reg_prob", 0.5),
            "primary_reason":   c.get("primary_reason") or raw.get("primary_reason", ""),
            "categories_interest": c.get("categories_interest") or raw.get("categories_interest", ""),
            "meeting_interest": raw.get("wants_meeting") or c.get("meeting_interest"),
            "purchase_timeline": raw.get("purchase_timeline", ""),
            "actively_sourcing": raw.get("actively_sourcing", False),
            "specific_product":  raw.get("specific_product_interest", ""),
            "company_size":      c.get("company_size", ""),
            "match_score":      score_data.get("matchScore", 50),
            "meeting_prob":  score_data.get("meetingProb", 0.5),
            "meeting_status": requested.get(c["id"]),
        })

    # Sort by match score descending
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results



class MatchAnalysisRequest(BaseModel):
    prospect: dict
    exhibitor: dict

@router.post("/match-analysis")
async def match_analysis(
    payload: MatchAnalysisRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generate Claude-powered intent match analysis between a prospect and exhibitor ICP."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    p   = payload.prospect
    ex  = payload.exhibitor

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
- LambdaMART match score: {round(float(p.get("match_score") or 0))}/100
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
                    "max_tokens": 1000,
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
        return json.loads(clean.strip())

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
    event_res = db.table("events").select("name, company").eq("id", payload.event_id).maybe_single().execute()
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
    event_res = db.table("events").select("name, company").eq("id", meeting["event_id"]).maybe_single().execute()
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
