"""
Fingoh IEI Scorer — Modal FastAPI endpoint v2
Implements the 41-signal Investigative Event Intelligence framework.

Changes vs v1:
  - Model v6: 82-feature input (41 signal values + 41 presence flags)
  - Updated tier thresholds: T1≥53, T2≥43, T3≥36 (calibrated to model output range)
  - Meeting model is now XGBClassifier (not a dict)
  - add_presence_flags() helper added
"""
import modal
import numpy as np
import pickle
from pathlib import Path

app = modal.App("fingoh-scorer-dev")

image = (
    modal.Image.debian_slim()
    .pip_install("xgboost", "scikit-learn", "numpy", "fastapi", "uvicorn")
)

volume  = modal.Volume.from_name("fingoh-model-vol-dev", create_if_missing=True)
MODEL_DIR = Path("/models")

# ── Tier thresholds (calibrated to v6 model output range) ───────────────────
T1_THRESHOLD = 53   # Hot
T2_THRESHOLD = 43   # Warm
T3_THRESHOLD = 36   # Cool
# < T3_THRESHOLD = Cold

SIGNAL_NAMES = [
    "reg_timing_days", "profile_completeness", "categories_specificity",
    "seniority_score", "icp_fit_score", "company_size_match",
    "app_session_count", "microsite_visits", "content_downloads",
    "email_click_rate", "session_reg_count", "social_mentions",
    "pre_event_content_eng", "email_open_rate",
    "meeting_requests_sent", "meeting_requests_received",
    "meeting_acceptance_rate", "meeting_no_show_rate",
    "private_room_bookings", "matchmaking_engagement",
    "badge_scan_count", "booth_dwell_time_min", "demo_attendance",
    "return_visits", "session_attend_ratio", "conv_quality_score",
    "questions_type_score", "collateral_specificity",
    "followup_response_hrs", "post_event_content_eng",
    "website_visit_post", "internal_content_share",
    "proposal_demo_request", "crm_stage_progression",
    "roi_report_published", "social_amplification",
    "buying_cycle_stage", "tech_stack_compatibility",
    "trigger_event_score", "previous_event_history",
    "competitive_displacement",
]


def add_presence_flags(x: np.ndarray) -> np.ndarray:
    """
    Append binary presence flags to signal vector.
    Input:  41-dim signal vector
    Output: 82-dim vector [values | presence_flags]
    Required for v6 model.
    """
    flags = (x > 0.01).astype(np.float32)
    return np.concatenate([x, flags])


def extract_features(visitor: dict) -> np.ndarray:
    """
    Map raw visitor data fields to the 41-signal feature vector.
    All values normalised to 0-1.
    """
    def safe(v, default=0.0):
        try:
            return float(v) if v is not None else default
        except:
            return default

    # ── Registration & Profile ───────────────────────────────────────────────
    reg_timing   = min(safe(visitor.get("reg_timing_days"), 0) / 90.0, 1.0)
    profile_compl = safe(visitor.get("profile_completeness"), 0.5)
    cat_spec      = safe(visitor.get("categories_specificity"), 0.3)

    # Seniority from title
    title = (visitor.get("job_title") or visitor.get("designation") or visitor.get("title") or "").lower()
    if any(x in title for x in ["ceo","cto","cfo","coo","chief","president","managing director","md"]):
        seniority = 1.0
    elif any(x in title for x in ["vp","vice president","svp","evp"]):
        seniority = 0.9
    elif any(x in title for x in ["director","head of","general manager"]):
        seniority = 0.75
    elif any(x in title for x in ["manager","senior","lead","principal"]):
        seniority = 0.55
    elif any(x in title for x in ["analyst","specialist","executive","consultant"]):
        seniority = 0.35
    else:
        seniority = safe(visitor.get("seniority_score"), 0.3)

    icp_fit      = safe(visitor.get("icp_fit_score"), 0.5)
    company_size = safe(visitor.get("company_size_match"), 0.5)

    # ── Digital Engagement ───────────────────────────────────────────────────
    app_sessions = min(safe(visitor.get("app_session_count"), 0) / 10.0, 1.0)
    microsite    = min(safe(visitor.get("microsite_visits"), 0) / 5.0, 1.0)
    downloads    = min(safe(visitor.get("content_downloads"), 0) / 5.0, 1.0)
    email_click  = safe(visitor.get("email_click_rate"), 0.0)
    session_regs = min(safe(visitor.get("session_reg_count"), 0) / 5.0, 1.0)
    social       = min(safe(visitor.get("social_mentions"), 0) / 3.0, 1.0)
    content_eng  = safe(visitor.get("pre_event_content_eng"), 0.0)
    email_open   = safe(visitor.get("email_open_rate"), 0.0)

    # ── Meeting & Scheduling ─────────────────────────────────────────────────
    mtg_sent     = min(safe(visitor.get("meeting_requests_sent"), 0) / 3.0, 1.0)
    mtg_received = min(safe(visitor.get("meeting_requests_received"), 0) / 5.0, 1.0)
    mtg_accept   = safe(visitor.get("meeting_acceptance_rate"), 0.0)
    mtg_noshow   = 1.0 - safe(visitor.get("meeting_no_show_rate"), 0.0)
    room_book    = min(safe(visitor.get("private_room_bookings"), 0) / 3.0, 1.0)
    matchmaking  = safe(visitor.get("matchmaking_engagement"), 0.0)

    # ── On-Site Behavioural ──────────────────────────────────────────────────
    badge_scan  = min(safe(visitor.get("badge_scan_count"), 0) / 5.0, 1.0)
    dwell       = min(safe(visitor.get("booth_dwell_time_min"), 0) / 15.0, 1.0)
    demo        = 1.0 if visitor.get("demo_attendance") else 0.0
    return_vis  = min(safe(visitor.get("return_visits"), 0) / 3.0, 1.0)
    session_att = safe(visitor.get("session_attend_ratio"), 0.0)
    conv_qual   = max(0.0, (safe(visitor.get("conv_quality_score"), 0) - 1.0) / 4.0)

    # Questions type
    q_type = visitor.get("questions_type", "")
    if isinstance(q_type, list):
        if "pricing" in q_type or "implementation" in q_type:
            q_score = 1.0
        elif "technical" in q_type:
            q_score = 0.7
        elif "competitive" in q_type:
            q_score = 0.5
        else:
            q_score = 0.2
    else:
        q_score = safe(visitor.get("questions_type_score"), 0.0)

    # Collateral specificity
    coll = visitor.get("collateral_requested", "")
    if coll == "specific":
        coll_score = 1.0
    elif coll == "generic":
        coll_score = 0.3
    else:
        coll_score = safe(visitor.get("collateral_specificity"), 0.0)

    # ── Post-Event Response ──────────────────────────────────────────────────
    resp_hrs   = max(0.0, 1.0 - min(safe(visitor.get("followup_response_hrs"), 999) / 168.0, 1.0))
    post_eng   = safe(visitor.get("post_event_content_eng"), 0.0)
    web_visit  = safe(visitor.get("website_visit_post"), 0.0)
    content_sh = safe(visitor.get("internal_content_share"), 0.0)
    proposal   = 1.0 if visitor.get("proposal_demo_request") else 0.0
    crm_stage  = safe(visitor.get("crm_stage_progression"), 0.0)
    roi_rep    = 1.0 if visitor.get("roi_report_published") else 0.0
    social_amp = safe(visitor.get("social_amplification"), 0.0)

    # ── Firmographic & Contextual ────────────────────────────────────────────
    buying_cyc = safe(visitor.get("buying_cycle_stage"), 0.0)
    tech_stack = safe(visitor.get("tech_stack_compatibility"), 0.0)
    trigger    = safe(visitor.get("trigger_event_score"), 0.0)
    prev_hist  = safe(visitor.get("previous_event_history"), 0.0)
    comp_disp  = safe(visitor.get("competitive_displacement"), 0.0)

    x41 = np.array([
        reg_timing, profile_compl, cat_spec, seniority, icp_fit, company_size,
        app_sessions, microsite, downloads, email_click, session_regs, social,
        content_eng, email_open,
        mtg_sent, mtg_received, mtg_accept, mtg_noshow, room_book, matchmaking,
        badge_scan, dwell, demo, return_vis, session_att, conv_qual,
        q_score, coll_score,
        resp_hrs, post_eng, web_visit, content_sh, proposal, crm_stage,
        roi_rep, social_amp,
        buying_cyc, tech_stack, trigger, prev_hist, comp_disp,
    ], dtype=np.float32)

    return x41


@app.cls(
    image=image,
    volumes={str(MODEL_DIR): volume},
)
class Scorer:
    @modal.enter()
    def load_models(self):
        iei_path     = MODEL_DIR / "iei_model.pkl"
        reg_path     = MODEL_DIR / "reg_model.pkl"
        meeting_path = MODEL_DIR / "meeting_model.pkl"
        version_path = MODEL_DIR / "model_version.txt"

        # Read model version
        self.model_version = "v1"
        if version_path.exists():
            self.model_version = version_path.read_text().strip()
        self.use_presence_flags = "presence-flags" in self.model_version
        print(f"Model version: {self.model_version}, presence_flags: {self.use_presence_flags}")

        if iei_path.exists() and reg_path.exists():
            with open(iei_path, "rb") as f:
                self.iei_model = pickle.load(f)
            with open(reg_path, "rb") as f:
                self.reg_model = pickle.load(f)
            print("✓ IEI + reg models loaded")
        else:
            self.iei_model = None
            self.reg_model = None
            print("⚠ IEI/reg models not found")

        if meeting_path.exists():
            with open(meeting_path, "rb") as f:
                raw = pickle.load(f)
            # v6: XGBClassifier directly; v1: dict wrapper
            if isinstance(raw, dict):
                self.meeting_model    = raw.get("model")
                self.meeting_use_prob = raw.get("use_xgb", False)
            else:
                self.meeting_model    = raw   # XGBClassifier
                self.meeting_use_prob = True
            print("✓ Meeting model loaded")
        else:
            self.meeting_model = None
            print("⚠ Meeting model not found")

    def _prepare_iei_input(self, x41: np.ndarray) -> np.ndarray:
        """Add presence flags if model v6+."""
        if self.use_presence_flags:
            return add_presence_flags(x41).reshape(1, -1)
        return x41.reshape(1, -1)

    @modal.fastapi_endpoint(method="POST")
    def score(self, payload: dict):
        visitors = payload.get("visitors", [])
        if not visitors:
            return {"scores": []}

        if self.iei_model is None:
            return {"scores": [
                {"ieiScore": 50.0, "regProb": 0.5, "ieiTier": "T2", "ieiTierLabel": "Warm"}
                for _ in visitors
            ]}

        results = []
        for v in visitors:
            try:
                x41 = extract_features(v)
                x   = self._prepare_iei_input(x41)

                iei = float(np.clip(self.iei_model.predict(x)[0], 0, 100))
                reg = float(np.clip(self.reg_model.predict(x)[0], 0, 1))

                # Calibrated thresholds for v6 model
                if iei >= T1_THRESHOLD:
                    tier, tier_label = "T1", "Hot"
                elif iei >= T2_THRESHOLD:
                    tier, tier_label = "T2", "Warm"
                elif iei >= T3_THRESHOLD:
                    tier, tier_label = "T3", "Cool"
                else:
                    tier, tier_label = "T4", "Cold"

                results.append({
                    "ieiScore":     round(iei, 2),
                    "regProb":      round(reg, 4),
                    "ieiTier":      tier,
                    "ieiTierLabel": tier_label,
                })
            except Exception as e:
                results.append({
                    "ieiScore": 43.0, "regProb": 0.43,
                    "ieiTier": "T2", "ieiTierLabel": "Warm",
                    "error": str(e)
                })

        return {"scores": results}

    @modal.fastapi_endpoint(method="POST")
    def meeting_score(self, payload: dict):
        """Score contacts for meeting acceptance probability."""
        visitors = payload.get("visitors", [])
        if not visitors:
            return {"scores": []}

        if self.meeting_model is None:
            return {"scores": [
                {"matchScore": round(min(float(v.get("iei_score", 50)), 100), 1),
                 "meetingProb": round(min(float(v.get("iei_score", 50)) / 100, 1), 3)}
                for v in visitors
            ]}

        def safe(v, default=0.0):
            try: return float(v) if v is not None else default
            except: return default

        results = []
        for v in visitors:
            try:
                # Extract 41-signal vector and use same features for meeting model
                x41 = extract_features(v)
                x   = x41.reshape(1, -1)

                if self.meeting_use_prob and hasattr(self.meeting_model, "predict_proba"):
                    meet_prob = float(self.meeting_model.predict_proba(x)[0][1])
                else:
                    raw = float(self.meeting_model.predict(x)[0])
                    meet_prob = float(np.clip(raw / 10.0, 0, 1))

                results.append({
                    "matchScore":  round(meet_prob * 100, 1),
                    "meetingProb": round(meet_prob, 3),
                })
            except Exception as e:
                results.append({"matchScore": 50.0, "meetingProb": 0.5, "error": str(e)})

        return {"scores": results}

    @modal.fastapi_endpoint(method="GET")
    def health(self):
        return {
            "status":                "ok",
            "models_loaded":         self.iei_model is not None,
            "meeting_model_loaded":  self.meeting_model is not None,
            "model_version":         self.model_version,
            "use_presence_flags":    self.use_presence_flags,
            "n_signals":             41,
            "n_features":            82 if self.use_presence_flags else 41,
            "tier_thresholds":       {"T1": T1_THRESHOLD, "T2": T2_THRESHOLD, "T3": T3_THRESHOLD},
            "version":               "3.0.0",
        }
