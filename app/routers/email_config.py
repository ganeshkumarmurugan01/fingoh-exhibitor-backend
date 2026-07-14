from fastapi import APIRouter, Depends
from app.auth import get_current_user, get_user_org
from app.database import get_db
from pydantic import BaseModel
from typing import Optional
import datetime

router = APIRouter(prefix="/email-config", tags=["email-config"])

class EmailConfigUpdate(BaseModel):
    logo_url:           Optional[str] = None
    primary_color:      Optional[str] = None
    banner_url:         Optional[str] = None
    sender_name:        Optional[str] = None
    reply_to:           Optional[str] = None
    signature_name:     Optional[str] = None
    signature_title:    Optional[str] = None
    signature_phone:    Optional[str] = None
    signature_linkedin: Optional[str] = None
    signature_company:  Optional[str] = None
    footer_text:        Optional[str] = None
    templates:          Optional[dict] = None

@router.get("/{event_id}")
def get_email_config(event_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    result = db.table("email_config").select("*").eq("event_id", event_id).maybe_single().execute()
    if result and result.data:
        return result.data
    return {
        "event_id": event_id, "logo_url": None, "primary_color": "#0F172A",
        "banner_url": None, "sender_name": None, "reply_to": None,
        "signature_name": None, "signature_title": None, "signature_phone": None,
        "signature_linkedin": None, "signature_company": None,
        "footer_text": "Sent via Fingoh · AI-powered event intelligence", "templates": {},
    }

@router.patch("/{event_id}")
def update_email_config(
    event_id: str, payload: EmailConfigUpdate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    org_id = get_user_org(current_user, db)
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    fields["updated_at"] = datetime.datetime.utcnow().isoformat()
    existing = db.table("email_config").select("id").eq("event_id", event_id).maybe_single().execute()
    if existing and existing.data:
        db.table("email_config").update(fields).eq("event_id", event_id).execute()
    else:
        fields["event_id"] = event_id
        fields["org_id"]   = org_id
        db.table("email_config").insert(fields).execute()
    return db.table("email_config").select("*").eq("event_id", event_id).maybe_single().execute().data


def get_email_config_for_event(db, event_id: str) -> dict:
    result = db.table("email_config").select("*").eq("event_id", event_id).maybe_single().execute()
    return (result.data or {}) if result else {}


def render_email_html(body_html: str, config: dict, visitor_name: str = "",
                      event_name: str = "", extra_vars: dict = None) -> str:
    extra_vars = extra_vars or {}
    primary    = config.get("primary_color") or "#0F172A"
    logo_url   = config.get("logo_url") or ""
    banner_url = config.get("banner_url") or ""
    sig_name   = config.get("signature_name") or config.get("sender_name") or "The Fingoh Team"
    sig_title  = config.get("signature_title") or ""
    sig_phone  = config.get("signature_phone") or ""
    sig_li     = config.get("signature_linkedin") or ""
    sig_co     = config.get("signature_company") or ""
    footer_txt = config.get("footer_text") or "Sent via Fingoh · AI-powered event intelligence"

    for tag, val in {"visitor_name": visitor_name, "event_name": event_name,
                     "sender_name": sig_name, "signature_name": sig_name,
                     "signature_title": sig_title, "signature_company": sig_co,
                     **extra_vars}.items():
        body_html = body_html.replace("{{" + tag + "}}", val or "")

    logo_html   = f'<img src="{logo_url}" alt="Logo" style="max-height:48px;margin-bottom:4px;">' if logo_url else ""
    banner_html = f'<img src="{banner_url}" alt="" style="width:100%;border-radius:0;">' if banner_url else ""
    sig_rows    = "".join([
        f'<p style="margin:2px 0;font-size:13px;font-weight:700;color:{primary};">{sig_name}</p>' if sig_name else "",
        f'<p style="margin:2px 0;font-size:12px;color:#64748B;">{sig_title}</p>' if sig_title else "",
        f'<p style="margin:2px 0;font-size:12px;color:#64748B;">{sig_co}</p>' if sig_co else "",
        f'<p style="margin:2px 0;font-size:12px;color:#64748B;">{sig_phone}</p>' if sig_phone else "",
        f'<p style="margin:2px 0;font-size:12px;"><a href="{sig_li}" style="color:{primary};">LinkedIn</a></p>' if sig_li else "",
    ])

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F8FAFC;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:32px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;">
<tr><td style="background:{primary};padding:20px 32px;">{logo_html}
  <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:12px;">{event_name}</p></td></tr>
{"<tr><td style='padding:0;'>" + banner_html + "</td></tr>" if banner_url else ""}
<tr><td style="padding:32px;color:#0F172A;font-size:14px;line-height:1.7;">{body_html}
  <div style="margin-top:24px;padding-top:16px;border-top:1px solid #E2E8F0;">{sig_rows}</div>
</td></tr>
<tr><td style="background:#F8FAFC;padding:14px 32px;border-top:1px solid #E2E8F0;text-align:center;">
  <p style="margin:0;font-size:11px;color:#94A3B8;">{footer_txt}</p></td></tr>
</table></td></tr></table></body></html>"""
