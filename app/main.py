from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import get_settings
from app.routers import onboarding, events, staff

settings = get_settings()

app = FastAPI(
    title="Fingoh Exhibitor API",
    version="1.0.0",
    description="Backend API for the Fingoh Exhibitor platform — intent intelligence for trade fair exhibitors.",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:5173",    # Vite dev server
        "http://localhost:4173",    # Vite preview
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global error handler ──────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all for unexpected errors.
    Returns a clean JSON response instead of a raw 500 traceback.
    In development, include the error detail for debugging.
    """
    if settings.debug:
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "type": type(exc).__name__},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
    )

# ── Routers ───────────────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"

app.include_router(onboarding.router, prefix=API_PREFIX)
app.include_router(events.router,     prefix=API_PREFIX)
app.include_router(staff.router,      prefix=API_PREFIX)

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
def health():
    """Used by Railway and uptime monitors to verify the service is running."""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/", tags=["system"])
def root():
    return {"message": "Fingoh Exhibitor API", "docs": "/docs"}
