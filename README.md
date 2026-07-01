# Fingoh Exhibitor — Backend API

FastAPI backend for the Fingoh Exhibitor platform.

## Stack

- **FastAPI** — web framework
- **Supabase** — PostgreSQL database + Auth
- **Railway** — deployment
- **Python 3.11+**

## Local setup

```bash
# 1. Clone and enter the directory
git clone https://github.com/your-org/fingoh-exhibitor-backend.git
cd fingoh-exhibitor-backend

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate      # Mac/Linux
.venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env with your Supabase credentials

# 5. Run locally
uvicorn app.main:app --reload
```

API docs will be available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc:       http://localhost:8000/redoc
- Health:      http://localhost:8000/health

## Run tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link to your Railway project
railway link

# Deploy
railway up
```

Set these environment variables in Railway dashboard → Variables:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_JWT_SECRET`
- `FRONTEND_URL`
- `APP_ENV=production`
- `DEBUG=false`

## API routes

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health check |
| GET | /api/v1/onboarding/me | Get current user profile |
| POST | /api/v1/onboarding/organisation | Create organisation (post-signup) |
| PATCH | /api/v1/onboarding/me | Update profile name/title |
| GET | /api/v1/events/ | List org events |
| POST | /api/v1/events/ | Create event (full wizard payload) |
| GET | /api/v1/events/{id} | Get event with full config |
| PATCH | /api/v1/events/{id} | Update core event fields |
| PATCH | /api/v1/events/{id}/icp | Update ICP config |
| PATCH | /api/v1/events/{id}/intent | Update exhibitor intent |
| DELETE | /api/v1/events/{id} | Archive event |
| GET | /api/v1/staff/ | List org staff |
| POST | /api/v1/staff/ | Add staff member |
| PATCH | /api/v1/staff/{id} | Update staff member |
| DELETE | /api/v1/staff/{id} | Remove staff member |
| POST | /api/v1/staff/verify-login | Staff App email login |
# meetings router added Wed Jul  1 17:16:14 IST 2026
