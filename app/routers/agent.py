"""
Fingoh Agent — endpoint that builds a real action queue for the 3 agents:
  outreach  -> T1/T2 contacts by pre-event IEI score, no meeting booked yet
  routing   -> contacts with onsite signals logged (have onsite_iei_score)
  followup  -> contacts whose meeting is completed
"""

import os
import re
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.database import get_db
from app.auth import get_current_user

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

router = APIRouter(prefix="/agent", tags=["agent"])

MAX_PER_BUCKET = 5


def _parse_draft(agent_id: str, text: str) -> dict:
    result = {"draft_text": text}
    if agent_id == "outreach":
        lines = text.strip().split("\n")
        subj  = lines[0].replace("Subject:", "").strip()
        body  = "\n".join(lines[2:]).strip() if len(lines) > 2 else text
        result.update({"email1_subject": subj, "email1_body": body})
    elif agent_id == "followup":
        m1 = re.search(r"EMAIL 1 \(Day 1\)[^\n]*\n(.*?)(?=---|LINKEDIN|EMAIL 2|$)", text, re.DOTALL)
        if m1:
            block  = m1.group(1).strip()
            blines = block.split("\n")
            subj   = blines[0].replace("Subject:", "").strip()
            body   = "\n".join(blines[1:]).strip()
            result.update({"email1_subject": subj, "email1_body": body})
        ml = re.search(r"LINKEDIN \(Day 3\)[^\n]*\n(.*?)(?=---|EMAIL 2|$)", text, re.DOTALL)
        if ml:
            result["linkedin_text"] = ml.group(1).strip()
        m2 = re.search(r"EMAIL 2 \(Day 7\)[^\n]*\n(.*?)$", text, re.DOTALL)
        if m2:
            block  = m2.group(1).strip()
            blines = block.split("\n")
            subj   = blines[0].replace("Subject:", "").strip()
            body   = "\n".join(blines[1:]).strip()
            result.update({"email2_subject": subj, "email2_body": body})
    return result


def _build_html_email(exhibitor: str, to_name: str, body: str) -> str:
    safe_body  = body.replace("\n", "<br>")
    first_name = to_name.split()[0] if to_name else "there"
    return (
        '<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">'
        '<div style="background:#0D1B3E;padding:18px 24px;border-radius:8px 8px 0 0;">'
        f'<span style="color:#ffffff;font-size:18px;font-weight:700;">{exhibitor}</span>'
        '</div>'
        '<div style="padding:28px 24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">'
        f'<p style="margin:0 0 16px;">Dear {first_name},</p>'
        f'<p style="margin:0 0 24px;line-height:1.6;">{safe_body}</p>'
        '<hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">'
        f'<p style="margin:0;font-size:12px;color:#6b7280;">This message was sent via <strong>Fingoh</strong> on behalf of {exhibitor}.</p>'
        '</div></div>'
    )


@router.get("/queue")
def get_agent_queue(event_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    ev = db.table("events").select("*").eq("id", event_id).maybe_single().execute()
    if not ev or not ev.data:
        raise HTTPException(status_code=404, detail="Event not found")
    event = ev.data

    contacts_res = db.table("audience_contacts").select(
        "id, name, email, company, designation, industry, "
        "iei_score, iei_tier, onsite_iei_score, onsite_iei_tier, iei_research"
    ).eq("event_id", event_id).execute()
    contacts = contacts_res.data or []

    meetings_res = db.table("meeting_requests").select(
        "contact_id, status"
    ).eq("event_id", event_id).execute()
    meetings = meetings_res.data or []
    has_meeting       = {m["contact_id"] for m in meetings}
    completed_meeting = {m["contact_id"] for m in meetings if m["status"] == "completed"}

    outreach_items, routing_items, followup_items = [], [], []

    for c in contacts:
        cid         = c["id"]
        iei         = c.get("iei_score") or 0
        tier        = c.get("iei_tier") or ""
        onsite      = c.get("onsite_iei_score")
        onsite_tier = c.get("onsite_iei_tier") or ""
        research    = c.get("iei_research") or {}
        reason_pre    = (research.get("summary") or "")[:120] if research else ""
        reason_onsite = (research.get("onsite_summary") or research.get("summary") or "")[:120] if research else ""

        base = {
            "id": cid, "visitor": c.get("name") or "Unknown",
            "company": c.get("company") or "", "role": c.get("designation") or "",
            "industry": c.get("industry") or "", "ieiScore": iei, "status": "pending",
        }

        if tier in ("T1", "T2") and iei >= 60 and cid not in has_meeting:
            outreach_items.append({**base, "agentId": "outreach",
                "reason": reason_pre or f"IEI score {iei}/100 — high propensity prospect, no outreach yet"})

        # Routing agent removed — reserved for future agent based on exhibitor feedback

        if cid in completed_meeting:
            followup_items.append({**base, "agentId": "followup", "ieiScore": onsite or iei,
                "reason": reason_onsite or f"Meeting completed — IEI {onsite or iei}/100, ready for follow-up"})

    for lst in [outreach_items, routing_items, followup_items]:
        lst.sort(key=lambda x: x["ieiScore"], reverse=True)

    queue = outreach_items[:MAX_PER_BUCKET] + followup_items[:MAX_PER_BUCKET]

    drafts_res = db.table("agent_outputs").select("*").eq("event_id", event_id).execute()
    drafts = {}
    for d in (drafts_res.data or []):
        key = d["contact_id"] + "_" + d["agent_id"]
        drafts[key] = d

    for item in queue:
        key = item["id"] + "_" + item["agentId"]
        if key in drafts:
            d = drafts[key]
            item["savedDraft"] = {
                "text":          d.get("draft_text"),
                "email1Subject": d.get("email1_subject"),
                "email1Body":    d.get("email1_body"),
                "linkedinText":  d.get("linkedin_text"),
                "email2Subject": d.get("email2_subject"),
                "email2Body":    d.get("email2_body"),
                "status":        d.get("status"),
            }

    return {
        "event": {
            "id": event.get("id"), "name": event.get("name"),
            "venue": event.get("venue"), "company": event.get("company"),
            "product": event.get("product"),
        },
        "queue": queue,
        "counts": {
            "outreach": len(outreach_items[:MAX_PER_BUCKET]),
            "followup": len(followup_items[:MAX_PER_BUCKET]),
        },
    }


class GenerateRequest(BaseModel):
    prompt:     str
    event_id:   str
    contact_id: str
    agent_id:   str


@router.post("/generate")
async def agent_generate(body: GenerateRequest, current_user: dict = Depends(get_current_user)):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": body.prompt}],
            },
        )

    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Anthropic error: {res.text[:200]}")

    text   = res.json()["content"][0]["text"]
    parsed = _parse_draft(body.agent_id, text)

    db = get_db()
    db.table("agent_outputs").upsert({
        "event_id":       body.event_id,
        "contact_id":     body.contact_id,
        "agent_id":       body.agent_id,
        "draft_text":     parsed.get("draft_text"),
        "email1_subject": parsed.get("email1_subject"),
        "email1_body":    parsed.get("email1_body"),
        "linkedin_text":  parsed.get("linkedin_text"),
        "email2_subject": parsed.get("email2_subject"),
        "email2_body":    parsed.get("email2_body"),
        "status":         "pending",
    }, on_conflict="event_id,contact_id,agent_id").execute()

    return {**parsed, "text": text}


class SendRequest(BaseModel):
    event_id:       str
    contact_id:     str
    agent_id:       str
    generated_text: str
    email_number:   int = 1


@router.post("/send")
async def agent_send(body: SendRequest, current_user: dict = Depends(get_current_user)):
    if body.agent_id == "routing":
        return {"status": "skipped", "reason": "Routing briefs are for staff — no email sent"}

    db = get_db()
    contact_res = db.table("audience_contacts").select("id, name, email, company") \
        .eq("id", body.contact_id).maybe_single().execute()
    if not contact_res or not contact_res.data:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact  = contact_res.data
    to_name  = contact.get("name") or "there"
    company  = contact.get("company") or ""
    to_email = contact.get("email") or ""
    if not to_email:
        raise HTTPException(status_code=400, detail="Contact has no email address")

    override = os.getenv("TEST_EMAIL_OVERRIDE", "")
    src = override if override else to_email
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", src)
    to_email = m.group(0) if m else src

    ev2 = db.table("events").select("company").eq("id", body.event_id).maybe_single().execute()
    exhibitor_name = (ev2.data.get("company") if ev2 and ev2.data else None) or "the exhibitor"

    saved = db.table("agent_outputs").select("*") \
        .eq("event_id", body.event_id).eq("contact_id", body.contact_id) \
        .eq("agent_id", body.agent_id).maybe_single().execute()

    if saved and saved.data:
        if body.email_number == 2:
            subject    = saved.data.get("email2_subject") or f"Following up — {company}"
            email_body = saved.data.get("email2_body") or ""
        else:
            subject    = saved.data.get("email1_subject") or f"Following up — {company}"
            email_body = saved.data.get("email1_body") or ""
    else:
        parsed     = _parse_draft(body.agent_id, body.generated_text)
        subject    = parsed.get("email1_subject") or f"Following up — {company}"
        email_body = parsed.get("email1_body") or body.generated_text

    html_body = _build_html_email(exhibitor_name, to_name, email_body)

    from app.routers.meetings import get_zoho_access_token
    ZOHO_ACCOUNT_ID = os.getenv("ZOHO_ACCOUNT_ID", "670863000000008002")
    ZOHO_FROM_EMAIL = os.getenv("ZOHO_FROM_EMAIL", "noreply@fingoh.ai")

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

    db.table("agent_outputs").update({"status": "sent"}) \
        .eq("event_id", body.event_id).eq("contact_id", body.contact_id) \
        .eq("agent_id", body.agent_id).execute()

    return {"status": "sent", "to": to_email, "subject": subject}
