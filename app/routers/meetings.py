"""
Fingoh Meeting Requests — endpoints for match scoring, request sending, and status tracking.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user, get_user_org
from app.database import get_db
from pydantic import BaseModel
from typing import Optional, List
import os, httpx, secrets, datetime

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


class MeetingComplete(BaseModel):
    staff_completion_notes: Optional[str] = None


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
    meeting_details: dict, exhibitor_company: str
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
        <h1 style="color: white; margin: 0; font-size: 22px;">Meeting Request</h1>
        <p style="color: rgba(255,255,255,0.7); margin: 4px 0 0 0; font-size: 14px;">from {exhibitor_company}</p>
      </div>
      <div style="background: white; padding: 24px; border: 1px solid #E2E8F0;">
        <p style="font-size: 16px; color: #1E293B;">Dear {to_name},</p>
        <p style="color: #475569;">{exhibitor_company} would like to schedule a meeting with you at the event.</p>
        
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
                    "subject":     f"Meeting Request from {exhibitor_company}",
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
    expires = datetime.datetime.fromisoformat(token_row["expires_at"].replace("Z",""))
    if datetime.datetime.utcnow() > expires:
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
    visitors_payload = []
    for c in contacts:
        raw = c.get("raw_data") or {}
        visitors_payload.append({
            "job_title":              c.get("designation", ""),
            "iei_score":              c.get("iei_score", 50),
            "icp_fit_score":          raw.get("icp_fit_score", 0.5),
            "buying_cycle_stage":     raw.get("buying_cycle_stage", 0.3),
            "microsite_visits":       raw.get("microsite_visits", 0),
            "content_downloads":      raw.get("content_downloads", 0),
            "email_click_rate":       raw.get("email_click_rate", 0),
            "meeting_requests_sent":  raw.get("meeting_requests_sent", 0),
            "profile_completeness":   raw.get("profile_completeness", 0.5),
            "categories_specificity": raw.get("categories_specificity", 0.3),
            "reg_timing_days":        raw.get("reg_timing_days", 45),
            "trigger_event_score":    raw.get("trigger_event_score", 0),
            "competitive_displacement": raw.get("competitive_displacement", 0),
            "previous_event_history": raw.get("previous_event_history", 0),
            "company_size_match":     raw.get("company_size_match", 0.5),
        })

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
        results.append({
            "contact_id":    c["id"],
            "name":          c.get("name", c.get("email", "Unknown")),
            "designation":   c.get("designation", "—"),
            "company":       c.get("company", "—"),
            "country":       c.get("country", "—"),
            "email":         c.get("email", ""),
            "iei_score":     c.get("iei_score", 0),
            "iei_tier":      c.get("iei_tier", "Cool"),
            "reg_prob":      c.get("reg_prob", 0.5),
            "match_score":   score_data.get("matchScore", 50),
            "meeting_prob":  score_data.get("meetingProb", 0.5),
            "meeting_status": requested.get(c["id"]),
        })

    # Sort by match score descending
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results


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
    db.table("meeting_requests").update({
        "status": "completed",
        "completed_at": datetime.datetime.utcnow().isoformat(),
        "staff_completion_notes": payload.staff_completion_notes,
    }).eq("id", meeting_id).execute()
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
