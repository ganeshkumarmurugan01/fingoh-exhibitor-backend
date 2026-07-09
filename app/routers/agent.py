"""
Fingoh Agent — endpoint that builds a real action queue for the 3 agents:
  outreach  → T1/T2 contacts by pre-event IEI score, no meeting booked yet
  routing   → contacts with onsite signals logged (have onsite_iei_score)
  followup  → contacts whose meeting is completed
"""

import os
from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db
from app.auth import get_current_user

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

router = APIRouter(prefix="/agent", tags=["agent"])

MAX_PER_BUCKET = 5   # max contacts per agent bucket


@router.get("/queue")
def get_agent_queue(
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()

    # ── 1. Fetch event context ────────────────────────────────────────────────
    ev = db.table("events").select("*").eq("id", event_id).maybe_single().execute()
    if not ev or not ev.data:
        raise HTTPException(status_code=404, detail="Event not found")
    event = ev.data

    # ── 2. Fetch all contacts for this event ──────────────────────────────────
    contacts_res = db.table("audience_contacts").select(
        "id, name, email, company, designation, industry, "
        "iei_score, iei_tier, onsite_iei_score, onsite_iei_tier, "
        "iei_research"
    ).eq("event_id", event_id).execute()
    contacts = contacts_res.data or []

    # ── 3. Fetch meeting_requests for this event ──────────────────────────────
    meetings_res = db.table("meeting_requests").select(
        "contact_id, status"
    ).eq("event_id", event_id).execute()
    meetings = meetings_res.data or []

    # Build lookup sets
    has_meeting       = {m["contact_id"] for m in meetings}
    completed_meeting = {m["contact_id"] for m in meetings if m["status"] == "completed"}

    # ── 4. Build agent buckets ────────────────────────────────────────────────

    outreach_items = []
    routing_items  = []
    followup_items = []

    for c in contacts:
        cid         = c["id"]
        iei         = c.get("iei_score") or 0
        tier        = c.get("iei_tier") or ""
        onsite      = c.get("onsite_iei_score")
        onsite_tier = c.get("onsite_iei_tier") or ""
        research    = c.get("iei_research") or {}

        # Extract a human-readable reason from iei_research if available
        reason_pre    = (research.get("summary") or "")[:120] if research else ""
        reason_onsite = (research.get("onsite_summary") or research.get("summary") or "")[:120] if research else ""

        base = {
            "id":       cid,
            "visitor":  c.get("name") or "Unknown",
            "company":  c.get("company") or "",
            "role":     c.get("designation") or "",
            "industry": c.get("industry") or "",
            "ieiScore": iei,
            "status":   "pending",
        }

        # OUTREACH — T1/T2 pre-event, no meeting booked yet
        if tier in ("T1", "T2") and iei >= 60 and cid not in has_meeting:
            outreach_items.append({
                **base,
                "agentId": "outreach",
                "reason":  reason_pre or f"IEI score {iei}/100 — high propensity prospect, no outreach yet",
            })

        # ROUTING — has onsite signals (conversation logged)
        if onsite is not None and cid not in completed_meeting:
            routing_items.append({
                **base,
                "agentId":    "routing",
                "ieiScore":   onsite or iei,
                "onsiteTier": onsite_tier,
                "reason":     reason_onsite or f"Onsite IEI {onsite}/100 — active on show floor",
            })

        # FOLLOWUP — completed meeting
        if cid in completed_meeting:
            followup_items.append({
                **base,
                "agentId": "followup",
                "ieiScore": onsite or iei,
                "reason":   reason_onsite or f"Meeting completed — IEI {onsite or iei}/100, ready for follow-up",
            })

    # Sort each bucket by score desc, cap at MAX_PER_BUCKET
    outreach_items.sort(key=lambda x: x["ieiScore"], reverse=True)
    routing_items.sort(key=lambda x: x["ieiScore"], reverse=True)
    followup_items.sort(key=lambda x: x["ieiScore"], reverse=True)

    queue = (
        outreach_items[:MAX_PER_BUCKET] +
        routing_items[:MAX_PER_BUCKET] +
        followup_items[:MAX_PER_BUCKET]
    )

    # ── 5. Return queue + event context for prompt building ──────────────────
    return {
        "event": {
            "id":      event.get("id"),
            "name":    event.get("name"),
            "venue":   event.get("venue"),
            "company": event.get("company"),
            "product": event.get("product"),
        },
        "queue": queue,
        "counts": {
            "outreach": len(outreach_items[:MAX_PER_BUCKET]),
            "routing":  len(routing_items[:MAX_PER_BUCKET]),
            "followup": len(followup_items[:MAX_PER_BUCKET]),
        }
    }


# ── Generate agent output via Anthropic (server-side) ───────────────────────
import httpx
import re
from pydantic import BaseModel

class GenerateRequest(BaseModel):
    prompt: str

@router.post("/generate")
async def agent_generate(
    body: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-opus-4-8",
                "max_tokens": 600,
                "messages":   [{"role": "user", "content": body.prompt}],
            },
        )

    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Anthropic error: {res.text[:200]}")

    data = res.json()
    return {"text": data["content"][0]["text"]}



# ── Send agent-generated email to contact ────────────────────────────────────
class SendRequest(BaseModel):
    event_id:       str
    contact_id:     str
    agent_id:       str   # "outreach" | "followup"
    generated_text: str

@router.post("/send")
async def agent_send(
    body: SendRequest,
    current_user: dict = Depends(get_current_user),
):
    if body.agent_id == "routing":
        return {"status": "skipped", "reason": "Routing briefs are for staff — no email sent"}

    db = get_db()

    # Fetch contact email + name
    contact_res = db.table("audience_contacts").select(
        "id, name, email, company"
    ).eq("id", body.contact_id).maybe_single().execute()

    if not contact_res or not contact_res.data:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact   = contact_res.data
    to_email  = contact.get("email") or ""
    to_name   = contact.get("name") or "there"
    company   = contact.get("company") or ""

    if not to_email:
        raise HTTPException(status_code=400, detail="Contact has no email address")

    # Parse subject + body from generated text
    # Outreach format: "Subject line\n\nbody..."
    # Followup format: "EMAIL 1 (Day 1) — Subject line\nthen body...---"
    lines = body.generated_text.strip().split("\n")

    if body.agent_id == "outreach":
        subject = lines[0].strip()
        email_body = "\n".join(lines[2:]).strip() if len(lines) > 2 else body.generated_text

    elif body.agent_id == "followup":
        # Extract just EMAIL 1 section (up to first ---)
        text = body.generated_text
        # Find subject after "EMAIL 1 (Day 1) —"
        import re
        day1_match = re.search(r"EMAIL 1 \(Day 1\)[^\n]*\n(.*?)(?=---|LINKEDIN|$)", text, re.DOTALL)
        if day1_match:
            day1_text  = day1_match.group(1).strip()
            day1_lines = day1_text.split("\n")
            subject    = day1_lines[0].strip()
            email_body = "\n".join(day1_lines[1:]).strip()
        else:
            subject    = f"Following up from the event — {company}"
            email_body = body.generated_text
    else:
        raise HTTPException(status_code=400, detail=f"Unknown agent_id: {body.agent_id}")

    # Send via Zoho Mail
    from app.routers.meetings import get_zoho_access_token
    ZOHO_ACCOUNT_ID = os.getenv("ZOHO_ACCOUNT_ID", "670863000000008002")
    ZOHO_FROM_EMAIL = os.getenv("ZOHO_FROM_EMAIL", "noreply@fingoh.ai")
    ZOHO_FROM_NAME  = os.getenv("ZOHO_FROM_NAME", "Fingoh")

    html_body = email_body.replace("\n", "<br>")

    try:
        access_token = await get_zoho_access_token()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
                headers={
                    "Authorization": f"Zoho-oauthtoken {access_token}",
                    "Content-Type":  "application/json",
                },
                json={
                    "fromAddress": ZOHO_FROM_EMAIL,
                    "toAddress":   to_email,
                    "subject":     subject,
                    "content":     html_body,
                },
            )
        print(f"[agent/send] Zoho response: {r.status_code} {r.text[:200]}")
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Zoho error: {r.text[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Email send failed: {str(e)}")

    return {
        "status":  "sent",
        "to":      to_email,
        "subject": subject,
    }
