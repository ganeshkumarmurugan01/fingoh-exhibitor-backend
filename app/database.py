from supabase import create_client, Client
from app.config import get_settings


def get_db() -> Client:
    """
    Returns a fresh Supabase client.
    Using a factory function instead of lru_cache to avoid
    issues with Railway's process model.
    """
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_service_key
    )
