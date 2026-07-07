from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.routers import onboarding, events, staff
from app.routers import audience
from app.routers import meetings
from app.routers import crm
from app.routers import admin

settings = get_settings()

app = FastAPI(redirect_slashes=False, title="Fingoh Exhibitor API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://fingoh-exhibitor.vercel.app",
        "https://fingoh-staff.vercel.app",
        "https://fingoh-staff-my7l3j4er-fingoh.vercel.app",
        "http://localhost:5173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(onboarding.router, prefix=API_PREFIX)
app.include_router(events.router, prefix=API_PREFIX)
app.include_router(staff.router, prefix=API_PREFIX)
app.include_router(audience.router, prefix=API_PREFIX)
app.include_router(meetings.router, prefix=API_PREFIX)
app.include_router(crm.router, prefix=API_PREFIX)
app.include_router(admin.router, prefix=API_PREFIX)

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/", tags=["system"])
def root():
    return {"message": "Fingoh Exhibitor API", "docs": "/docs"}