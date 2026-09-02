"""
client_config.py
----------------
Central configuration for white-label / dedicated client instances.
All client-specific behaviour is controlled here via environment variables.

For fingoh.ai production: defaults are used (no env vars needed).
For client instances: set these env vars in Railway per deployment.

NEVER hardcode client-specific values in core routers.
All customisation must flow through this config.
"""

import os
import json
import logging

logger = logging.getLogger("fingoh.client_config")


# ── Identity ──────────────────────────────────────────────────────────────────
CLIENT_NAME         = os.getenv("CLIENT_NAME", "Fingoh")
CLIENT_DOMAIN       = os.getenv("CLIENT_DOMAIN", "fingoh.ai")
CLIENT_SUPPORT_EMAIL= os.getenv("CLIENT_SUPPORT_EMAIL", "hello@fingoh.ai")
CLIENT_VERTICAL     = os.getenv("CLIENT_VERTICAL", "pharma")  # pharma | electronics | logistics | general
SHOW_FINGOH_BADGE   = os.getenv("SHOW_FINGOH_BADGE", "true").lower() == "true"

# ── Feature Flags ─────────────────────────────────────────────────────────────
# Control which features are enabled for this client instance
FEATURES = {
    "deep_iei":           os.getenv("FEATURE_DEEP_IEI", "true").lower() == "true",
    "agent_outreach":     os.getenv("FEATURE_AGENT", "true").lower() == "true",
    "organiser_module":   os.getenv("FEATURE_ORGANISER", "true").lower() == "true",
    "meeting_match":      os.getenv("FEATURE_MEETINGS", "true").lower() == "true",
    "pharma_intel":       os.getenv("FEATURE_PHARMA_INTEL", "true").lower() == "true",
    "walk_in_capture":    os.getenv("FEATURE_WALK_IN", "true").lower() == "true",
    "csv_export":         os.getenv("FEATURE_CSV_EXPORT", "true").lower() == "true",
    "crm_integration":    os.getenv("FEATURE_CRM", "true").lower() == "true",
    "custom_fields":      os.getenv("FEATURE_CUSTOM_FIELDS", "false").lower() == "true",
}

# ── Scoring Configuration ─────────────────────────────────────────────────────
# Tier thresholds — override per client if needed
TIER_THRESHOLDS = {
    "T1": int(os.getenv("TIER_T1_MIN", "62")),
    "T2": int(os.getenv("TIER_T2_MIN", "44")),
    "T3": int(os.getenv("TIER_T3_MIN", "34")),
}

# IEI credits per plan (can be overridden per client)
IEI_CREDITS_DEFAULT = int(os.getenv("IEI_CREDITS_DEFAULT", "100"))

# ── Custom Fields ─────────────────────────────────────────────────────────────
# Extra registration fields for this client
# Format: JSON array of field definitions
# Example: '[{"name":"badge_number","label":"Badge Number","type":"text","required":false}]'
_custom_fields_raw = os.getenv("CLIENT_CUSTOM_FIELDS", "[]")
try:
    CUSTOM_REGISTRATION_FIELDS = json.loads(_custom_fields_raw)
except json.JSONDecodeError:
    logger.warning("[client_config] Invalid CLIENT_CUSTOM_FIELDS JSON — using empty list")
    CUSTOM_REGISTRATION_FIELDS = []

# ── Email Configuration ───────────────────────────────────────────────────────
EMAIL_FROM_NAME    = os.getenv("ZOHO_FROM_NAME", CLIENT_NAME)
EMAIL_FROM_ADDRESS = os.getenv("ZOHO_FROM_EMAIL", "noreply@fingoh.ai")
EMAIL_SUPPORT      = CLIENT_SUPPORT_EMAIL

# ── Industry Intel Sources ────────────────────────────────────────────────────
# Which industry intel feed to use for enrichment context
INTEL_INDUSTRY = CLIENT_VERTICAL  # maps to pharma_intel_cache.industry

# ── Enrichment Configuration ──────────────────────────────────────────────────
ENRICHMENT_BATCH_SIZE    = int(os.getenv("ENRICHMENT_BATCH_SIZE", "50"))
ENRICHMENT_JUNK_FILTER   = os.getenv("ENRICHMENT_JUNK_FILTER", "true").lower() == "true"
ENRICHMENT_MAX_TOKENS    = int(os.getenv("ENRICHMENT_MAX_TOKENS", "700"))

# ── Branding (returned via API for frontend consumption) ──────────────────────
def get_branding() -> dict:
    """
    Returns branding config for frontend.
    Called by /api/v1/onboarding/client-config endpoint.
    """
    return {
        "name":             CLIENT_NAME,
        "domain":           CLIENT_DOMAIN,
        "support_email":    CLIENT_SUPPORT_EMAIL,
        "show_fingoh_badge":SHOW_FINGOH_BADGE,
        "vertical":         CLIENT_VERTICAL,
        "features":         FEATURES,
        "tier_thresholds":  TIER_THRESHOLDS,
        "custom_fields":    CUSTOM_REGISTRATION_FIELDS,
    }


def get_tier(iei_score: float) -> str:
    """Compute IEI tier using client-configured thresholds."""
    if iei_score >= TIER_THRESHOLDS["T1"]:
        return "T1"
    elif iei_score >= TIER_THRESHOLDS["T2"]:
        return "T2"
    elif iei_score >= TIER_THRESHOLDS["T3"]:
        return "T3"
    else:
        return "T4"


def is_feature_enabled(feature: str) -> bool:
    """Check if a feature is enabled for this client instance."""
    return FEATURES.get(feature, False)


# ── Log active config on startup ──────────────────────────────────────────────
logger.info(
    f"[client_config] Instance: {CLIENT_NAME} | "
    f"Vertical: {CLIENT_VERTICAL} | "
    f"Domain: {CLIENT_DOMAIN} | "
    f"Features: {[k for k,v in FEATURES.items() if v]}"
)
