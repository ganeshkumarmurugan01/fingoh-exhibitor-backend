from supabase import create_client, Client
from app.config import get_settings
from functools import lru_cache


@lru_cache()
def get_db() -> Client:
    """
    Returns a cached Supabase client using the service role key.
    The service key bypasses Row Level Security — use only in the backend.
    All user-scoped queries must manually filter by org_id.
    """
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_key)
