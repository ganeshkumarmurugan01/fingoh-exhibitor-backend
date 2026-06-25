from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from app.config import get_settings
from app.routers import onboarding, events, staff

settings = get_settings()

app = FastAPI(title="Fingoh Exhibitor API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(onboarding.router, prefix=API_PREFIX)
app.include_router(events.router, prefix=API_PREFIX)
app.include_router(staff.router, prefix=API_PREFIX)

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/", tags=["system"])
def root():
    return {"message": "Fingoh Exhibitor API", "docs": "/docs"}
