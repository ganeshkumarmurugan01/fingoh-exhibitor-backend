from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import date


# ── Sub-models ────────────────────────────────────────────────────────────────

class ICPConfig(BaseModel):
    roles: List[str] = []
    company_sizes: List[str] = []
    visit_reasons: List[str] = []


class IntentConfig(BaseModel):
    intent_why: Optional[str] = None
    intent_buyers: Optional[str] = None
    intent_signals: List[dict] = []
    buyer_signals: List[dict] = []


# ── Requests ──────────────────────────────────────────────────────────────────

class EventCreate(BaseModel):
    """
    Payload from the 5-step Create Event Wizard.
    All five steps are submitted together on the final step.
    """
    # Step 1 — Exhibition details
    name: str = Field(..., min_length=2, max_length=200)
    type: str = Field(..., description="Exhibition type ID, e.g. 'medtech'")
    type_label: str = Field(..., description="Human-readable label, e.g. 'MedTech & Healthcare'")
    date_from: date
    date_to: date
    venue: str = Field(..., min_length=2, max_length=300)
    country: str

    # Step 2 — Company & booth
    company: str = Field(..., min_length=1, max_length=200)
    product: str = Field(..., min_length=1, max_length=500)
    website: Optional[str] = None
    booth_size: Optional[str] = None

    # Step 3 — Target categories
    categories: List[str] = []

    # Step 4 — ICP
    icp_roles: List[str] = []
    icp_company_sizes: List[str] = []
    icp_visit_reasons: List[str] = []

    # Step 5 — Exhibitor intent
    intent_why: Optional[str] = None
    intent_buyers: Optional[str] = None
    intent_signals: List[dict] = []
    buyer_signals: List[dict] = []


class EventUpdate(BaseModel):
    """Partial update — all fields optional."""
    name: Optional[str] = None
    venue: Optional[str] = None
    country: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    company: Optional[str] = None
    product: Optional[str] = None
    website: Optional[str] = None
    booth_size: Optional[str] = None


class TargetingUpdate(BaseModel):
    """Update categories, ICP, and exhibitor intent — all optional, partial update."""
    categories: Optional[List[str]] = None
    icp_roles: Optional[List[str]] = None
    icp_company_sizes: Optional[List[str]] = None
    icp_visit_reasons: Optional[List[str]] = None
    intent_why: Optional[str] = None
    intent_buyers: Optional[str] = None
    intent_signals: Optional[List[dict]] = None
    buyer_signals: Optional[List[dict]] = None


# ── Responses ─────────────────────────────────────────────────────────────────

class EventResponse(BaseModel):
    id: str
    org_id: str
    name: str
    type: str
    type_label: Optional[str]
    date_from: Optional[Any]    # date or string from DB
    date_to: Optional[Any]
    venue: Optional[str]
    country: Optional[str]
    company: Optional[str]
    product: Optional[str]
    website: Optional[str]
    booth_size: Optional[str]
    status: str
    created_by: Optional[str]
    created_at: str
    updated_at: Optional[str]


class EventDetailResponse(EventResponse):
    """Full event including related config tables."""
    categories: List[str] = []
    icp: dict = {}
    intent: dict = {}
