"""Shared utility functions for routers."""
import logging
from app.database import get_db

logger = logging.getLogger("fingoh.activity")


def log_activity(db, org_id: str, action: str, description: str, user_id: str = None, metadata: dict = None):
    try:
        db.table("activity_logs").insert({
            "org_id":      org_id,
            "user_id":     user_id,
            "action":      action,
            "description": description,
            "metadata":    metadata or {},
        }).execute()
    except Exception as e:
        logger.error("Log failed: %s", e)
