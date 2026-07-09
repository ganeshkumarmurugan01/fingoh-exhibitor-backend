"""
Fingoh Agent — endpoint that builds a real action queue for the 3 agents:
  outreach  → T1/T2 contacts by pre-event IEI score, no meeting booked yet
  routing   → contacts with onsite signals logged (have onsite_iei_score)
  followup  → contacts whose meeting is completed
"""

from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db
from app.auth import get_current_user

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
