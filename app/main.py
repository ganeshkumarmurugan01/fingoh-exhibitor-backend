import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
from app.routers import onboarding, events, staff
from app.routers import audience
from app.routers import meetings
from app.routers import crm
from app.routers import salesforce
from app.routers import salesforce
from app.routers import admin
from app.routers import agent
from app.routers import email_config
from app.routers import offerings
from app.routers import organiser
from app.routers import products
from app.routers import categories

settings = get_settings()

app = FastAPI(redirect_slashes=False, title="Fingoh Exhibitor API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://fingoh-exhibitor.vercel.app",
        "https://fingoh-staff.vercel.app",
        "https://fingoh-admin.vercel.app",
        "https://exhibitor.fingoh.ai",
        "https://staff.fingoh.ai",
        "https://admin.fingoh.ai",
        "https://fingoh.ai",
        "https://www.fingoh.ai",
        "http://localhost:5173",
        "http://localhost:3000",
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
app.include_router(salesforce.router, prefix=API_PREFIX)
app.include_router(salesforce.router, prefix=API_PREFIX)
app.include_router(admin.router, prefix=API_PREFIX)
app.include_router(agent.router, prefix=API_PREFIX)
app.include_router(email_config.router, prefix=API_PREFIX)
app.include_router(offerings.router, prefix=API_PREFIX)
app.include_router(products.router, prefix="/api/v1")
app.include_router(categories.router, prefix="/api/v1")
app.include_router(organiser.router, prefix=API_PREFIX)

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/", tags=["system"])
def root():
    return {"message": "Fingoh Exhibitor API", "docs": "/docs"}