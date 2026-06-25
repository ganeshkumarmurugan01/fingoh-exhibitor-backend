from fastapi import APIRouter
from app.database import get_db
from app.config import get_settings

router = APIRouter(prefix="/debug", tags=["debug"])

@router.get("/env")
def check_env():
    settings = get_settings()
    return {
        "supabase_url": settings.supabase_url,
        "service_key_starts": settings.supabase_service_key[:20],
        "jwt_secret_starts": settings.supabase_jwt_secret[:10],
    }

@router.get("/db")
def check_db():
    try:
        db = get_db()
        result = db.table("profiles").select("id").limit(1).execute()
        return {"status": "connected", "data": result.data}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
