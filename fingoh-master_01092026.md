# Fingoh Platform — Master Development Reference

> **Last updated:** Sep 1, 2026  
> **Update this file at the end of every chat session.**

---

## 1. Platform Overview

Fingoh is a B2B trade fair exhibitor intelligence SaaS platform. It helps exhibitors identify, score, and engage high-intent visitors at trade fairs using ML scoring, AI enrichment, and real-time staff tooling.

**Three-tier hierarchy:** Fingoh Admin → Organiser → Exhibitors

---

## 2. Architecture

### Services

| Service | Dev URL | Prod URL | Hosting |
|---------|---------|----------|---------|
| Exhibitor App | `fingoh-exhibitor-[hash]-fingoh.vercel.app` | `exhibitor.fingoh.ai` | Vercel |
| Organiser Portal | `fingoh-organiser-[hash]-fingoh.vercel.app` | `organiser.fingoh.ai` | Vercel |
| Admin Panel | Vercel preview | `admin.fingoh.ai` | Vercel |
| Staff PWA | `fingoh-staff-[hash]-fingoh.vercel.app` | `staff.fingoh.ai` | Vercel |
| Backend API | `api-dev.fingoh.ai` | `api.fingoh.ai` | Railway |
| Supabase Dev | `oalmnxravzpdhlinswfi` | — | Supabase |
| Supabase Prod | — | `qftbpixjwkmmusppkhzi` | Supabase |
| ML Scoring | `fingoh-scorer-dev` / `fingoh-model-vol-dev` | `fingoh-scorer` / `fingoh-model-vol` | Modal |
| Docs | — | `docs.fingoh.ai` | Vercel (fingohdocs repo) |

### Tech Stack
- **Frontend:** React/Vite (exhibitor, organiser, admin) + Vanilla JS PWA (staff)
- **Backend:** FastAPI (Python)
- **Database & Auth:** Supabase (Postgres + Auth)
- **ML Scoring:** Modal (XGBoost IEI scorer — general + pharma + electronics + logistics; LambdaMART meeting scorer)
- **AI Enrichment:** Anthropic Claude (`claude-sonnet-4-6` / Opus for agent outputs)
- **Email:** Zoho Mail (`noreply@fingoh.ai`, account `hello@fingoh.ai`)
- **DNS/CDN:** Cloudflare (DNS-only for `api-dev.fingoh.ai` — no proxying)
- **CRM:** Zoho CRM (OAuth integrated)

---

## 3. Repositories

| Repo | GitHub | Prod Branch | Dev Branch |
|------|--------|-------------|------------|
| `fingoh-exhibitor` | `ganeshkumarmurugan01/fingoh-exhibitor` | `main` | `dev` |
| `fingoh-exhibitor-backend` | `ganeshkumarmurugan01/fingoh-exhibitor-backend` | `main` | `dev` |
| `fingoh-admin` | `ganeshkumarmurugan01/fingoh-admin` | `main` | `main` (no dev branch) |
| `fingoh-staff` | `ganeshkumarmurugan01/fingoh-staff` | `main` | `dev` |
| `fingoh-organiser` | `ganeshkumarmurugan01/fingoh-organiser` | `main` | `dev` |
| `fingohdocs` | `ganeshkumarmurugan01/fingohdocs` | `main` | `main` |
| `fingoh-xgboost-scorer` | `ganeshkumarmurugan01/fingoh-xgboost-scorer` | `main` | `main` |

**Local root:** `~/fingoh-exhibitor-root/`

---

## 4. Dev Workflow

1. All changes go to `dev` branch first
2. Test on Vercel preview URLs and `api-dev.fingoh.ai`
3. Create PR on GitHub from `dev` → `main`
4. Merge on GitHub (never push directly to `main`)
5. Railway and Vercel auto-deploy on merge

**When resolving `vercel.json` conflicts in PRs:** always accept the incoming `main` branch version to preserve the production backend URL.

---

## 5. Test Accounts

| Role | Email | Password | Notes |
|------|-------|----------|-------|
| Fingoh Admin | `test@fingoh.com` | `DevTest@123` | Super admin |
| Exhibitor | `ganesh@akiraas.com` | — | Akiraas Ptd Ltd, scale plan, org_id: `81fad930-7eda-4b78-b788-a754b0036eae` |
| Organiser (dev) | `test@test.com` | `test123` | organiser_id: `0401d7a7-da7d-4812-a447-a2e087499834` |
| Organiser (prod) | `admin@akiraas.com` | `Test@1234` | AKiraas Pte, organiser portal |
| Staff | `ganesh@akiraas.com` | passcode: `1234` | Electronics Expo event |

---

## 6. Backend Routers

| File | Prefix | Purpose |
|------|--------|---------|
| `onboarding.py` | `/api/v1/onboarding` | Auth, profile, org setup |
| `events.py` | `/api/v1/events` | Event CRUD, plan info |
| `audience.py` | `/api/v1/audience` | CSV upload, IEI scoring, contacts, deep research |
| `meetings.py` | `/api/v1/meetings` | Meeting requests, email, scoring |
| `crm.py` | `/api/v1/crm` | Zoho CRM OAuth, lead export |
| `admin.py` | `/api/v1/admin` | Customer management, stats |
| `staff.py` | `/api/v1/staff` | Staff login, signal logging |
| `organiser.py` | `/api/v1/organiser` | Full organiser module |
| `products.py` | `/api/v1/products` | Product file uploads |
| `categories.py` | `/api/v1/categories` | Category master CRUD |
| `pharma_intel.py` | — | Pharma intel cache fetcher + scheduler |
| `utils.py` | — | Shared `log_activity` helper |

**Critical:** Never import `log_activity` from `admin.py` — always import from `utils.py`.

---

## 7. Database Tables

### Core Tables
- `organisations` — exhibitor orgs
- `profiles` — user profiles
- `events` — exhibitor events (has `enrichment_paused`)
- `event_categories` — event category selections
- `event_icp` — ICP configuration
- `event_intent` — exhibitor intent config (has `why`, `buyers`, `signals`)
- `event_offerings` — products & services (has `category_master` JSONB)
- `offering_assets` — product file uploads
- `audience_contacts` — visitor contacts with IEI scores (see key columns below)
- `conversation_signals` — staff conversation logs
- `meeting_requests` — meeting management
- `meeting_tokens` — public token-based meeting responses
- `staff` — staff members (JWT auth, NOT Supabase auth)
- `activity_logs` — audit trail
- `plan_configs` — plan feature definitions
- `category_master` — pharma taxonomy (365 categories, 3 levels)
- `pharma_intel_cache` — daily pharma headline cache

### Key Column Notes — `audience_contacts`
- `iei_tier` is a **generated column** on prod Supabase — **never include in UPDATE calls** (error 428C9)
- `category_match_score FLOAT DEFAULT 0.0` — semantic match score from Claude enrichment
- `match_reasoning TEXT` — Claude's explanation for the category match score
- `enrichment_status TEXT` — values: pending, enriching, done, failed, skipped

---

## 8. API Routing

All API calls from exhibitor/organiser/admin frontend go through the Vercel proxy:
- **JSON calls:** `/api/proxy?slug=v1/...` → `BACKEND_URL/api/v1/...`
- **Multipart uploads:** `/api/upload.js` (bodyParser: false) → backend

Auth header: `x-fingoh-auth: Bearer <token>` (Vercel strips `Authorization` headers)

**Critical:** Always use `supabase.auth.getSession()` for auth tokens — never `localStorage.getItem("sb_token")`.

**Staff App:** Uses `const API = "https://api.fingoh.ai/api/v1"` — calls backend directly (no Vercel proxy). CORS is configured for `staff.fingoh.ai` in `main.py`.

---

## 9. ML Scoring

### IEI Scorer (XGBoost)
| Vertical | Dev Modal App | Prod Modal App | Volume (Dev) | Volume (Prod) |
|----------|--------------|----------------|--------------|---------------|
| General | `fingoh-scorer-dev` | `fingoh-scorer` | `fingoh-model-vol-dev` | `fingoh-model-vol` |
| Pharma | `fingoh-scorer-pharma-dev` | `fingoh-scorer-pharma` | `fingoh-model-vol-pharma-dev` | `fingoh-model-vol-pharma` |

### Pharma Scorer v4 (Current)
- **48 signals**, 96 features (48 + 48 presence flags)
- R² = 0.886, MAE = 4.86
- Tier thresholds: T1 ≥ 62, T2 ≥ 44, T3 ≥ 34, T4 < 34
- Signal 47: `category_match_score`
- Model version: `v4-pharma-presence-flags-96features`, Scorer: `4.0.0`

### Meeting Match Score (5-Dimension Bilateral)
Implemented directly in `meetings.py` — no Modal dependency.

```
MatchScore(v,e) = w1×intent_alignment + w2×icp_bilateral_fit + 
                 w3×tier_correlation + w4×timing_alignment + w5×prior_engagement
w = [0.35, 0.25, 0.20, 0.12, 0.08]
```

**Dimension 1 — Intent Alignment (35%):**
- Sourcing keywords → 0.75 base; research → 0.40; unknown → 0.30
- `meeting_interest=yes` → +0.25 (enriched) or +0.12 (unenriched)
- `category_match_score` → up to +0.15
- `procurement_mandate_score` → up to +0.20

**Dimension 2 — ICP Bilateral Fit (25%):**
- Role match against exhibitor ICP roles
- If Claude `icp_fit_score` available: `icp_fit*0.6 + role_score*0.25 + size_score*0.15`

**Dimension 3 — Tier Correlation (20%):**
- T1=1.0, T2=0.75, T3=0.40, T4=0.15
- Unenriched contacts: tier_correlation × 0.5
- T4 hard cap: overall ≤ 35; unenriched (skipped/failed) cap: ≤ 55

**Dimension 4 — Timing Alignment (12%):**
- Immediate → 1.0; 3mo → 0.80; 6mo → 0.60; 12mo → 0.40; unknown → 0.45

**Dimension 5 — Prior Engagement (8%):**
- microsite, email_click, content_downloads, previous_event_history, repeat_buyer, reg_prob

**Handoff file for match.fingoh.ai:** `fingoh_match_score_handoff.md`

---

## 10. Pharma Intel Cache (Live — Aug 26, 2026)

### Sources (7 RSS feeds, ~35 headlines/day)
| Source | RSS URL |
|--------|---------|
| FiercePharma | `https://www.fiercepharma.com/rss/xml` |
| World Pharma News | `https://www.worldpharmanews.com/?format=feed&type=rss` |
| PharmaTimes | `https://pharmatimes.com/feed` |
| PharmaVoice | `https://pharmavoice.com/feeds/news` |
| Economic Times Pharma | `https://pharma.economictimes.indiatimes.com/rss/topstories` |
| Pharmafile | `https://pharmafile.com/feed` |
| STAT News Pharma | `https://www.statnews.com/category/pharma/feed/` |

- APScheduler: daily 06:00 UTC + 30s startup run
- Wired into `main.py` lifespan via `start_intel_scheduler(sb)`
- `get_cached_intel(sb, industry, limit=10)` — called from `_enrich_visitor()`
- Failed sources (Railway IP blocked): CPhI Online, Pharmaceutical Technology

---

## 11. Enrichment Pipeline

### Trigger paths
| Entry point | Enrichment |
|-------------|-----------|
| CSV upload | ✅ Background batch auto-triggers |
| Manual add (form) | ✅ Goes through CSV upload path |
| Organiser import | ✅ Background enrichment fires after rescore_all |
| ⚡ Enrich selected | ✅ Force enrich bypasses junk filter |

### Enhanced prompt includes
1. Visitor registration data (capitalized CSV key fallbacks)
2. Exhibitor categories with descriptions from `category_master`
3. Exhibitor intent (`event_intent.why`, `buyers`, `signals`)
4. Exhibitor ICP (roles, company sizes, visit reasons)
5. Top 10 pharma intel headlines from `pharma_intel_cache`

### New output fields
- `category_match_score` (0.0–1.0): semantic match, saved to dedicated DB column
- `match_reasoning` (text): saved to dedicated DB column, fed to XGBoost

---

## 12. Staff App (staff.fingoh.ai)

### Critical: File Structure
```
fingoh-staff/
  public/          ← Vercel serves from HERE
    index.html     ← EDIT THIS FILE (not root index.html)
    sw.js          ← EDIT THIS FILE
    vercel.json    ← EDIT THIS FILE
    manifest.json
    icons/
  index.html       ← ROOT — not served by Vercel, ignore
  sw.js            ← ROOT — not served by Vercel, ignore
  vercel.json      ← ROOT — not served by Vercel, ignore
```

**Always edit files in `public/` directory. Root files are NOT deployed.**

### API Configuration
```javascript
const API = "https://api.fingoh.ai/api/v1";  // Direct — no Vercel proxy
```
CORS configured in `main.py` for `staff.fingoh.ai`.

### Service Worker
- Version: `fingoh-staff-v8`
- POST requests: passed through directly (no cloning — iOS Safari loses method on clone)
- GET requests: cache-first for static assets
- API calls: always network, returns `{error:'offline', status:503}` on failure
- Has `skipWaiting` message handler

### Offline Architecture
- **IndexedDB stores:** `queue` (pending syncs), `visitors_cache` (visitor list)
- **DB version:** 2
- Visitor list cached on login, served from IndexedDB when offline
- Signal logging queues to IndexedDB on any network error
- Sync queue uses `audience/log-signal/{event_id}` endpoint
- Auto-sync: every 30s when online + on `online` event

### Key Offline Patterns
```javascript
// Always queue on catch — covers all iOS error types
} catch(e) {
  await enqueue('signal', payload);  // Queue locally
  // Show "Saved offline" banner
}

// Sync queue endpoint
url = `${API}/audience/log-signal/${payload.event_id}`;
```

### Auth
- Staff login: `POST /staff/verify-login` — passcode-based, no JWT
- Session persisted to `sessionStorage` as `fingoh_staff`
- Token field: empty (endpoints are scoped by event_id, no auth required)

### Screens
1. **Login** — email → event select → passcode
2. **Choice** — Prospect (existing) or Walk-in (new)
3. **Log Signal (7 steps)** — find visitor, conversation quality, questions, on-site signals, meeting/collateral, urgency, notes
4. **Walk-in capture** — business card scan or manual form
5. **Meetings tab** — upcoming + completed, check-in/out, AI analyse

---

## 13. Meeting Match Filter (New — Aug 28, 2026)

Search + filter bar in Meeting Match prospects tab:
- Search by name, company, country
- Filter by IEI tier (T1/T2/T3/T4)
- Filter by match score (High ≥70 / Mid 50-69 / Low <50)
- Filter by meeting status (Not sent / Sent / Accepted / Completed)
- Live count + Clear button

State vars: `meetSearch`, `meetTier`, `meetMatch`, `meetStatus`

---

## 14. Key Architecture Rules

1. **`iei_tier` generated column on prod** — never include in UPDATE calls (428C9)
2. **`maybe_single()` guard** — always check `if not result or not result.data:`
3. **Circular imports** — never import `log_activity` from `admin.py` — always `utils.py`
4. **Route ordering** — specific FastAPI routes before parameterized ones
5. **Vercel proxy** — all exhibitor/organiser/admin API calls via `/api/proxy`
6. **Staff App** — direct API calls to `api.fingoh.ai` (CORS configured)
7. **Staff App files** — always edit `public/` directory, not root
8. **Modal dev/prod** — fully separated, never retrain against prod volume
9. **Frontend auth** — always `supabase.auth.getSession()` not `localStorage`
10. **meeting_interest** — stored as string `"yes"`/`"no"` not boolean
11. **primary_reason** — may be pipe-separated, always `.replace("|", " ")` before keyword matching
12. **iOS Safari SW** — never clone POST requests (method gets lost), pass through directly

---

## 15. Prod Supabase Schema (Applied)

```sql
-- pharma intel cache (Aug 26)
CREATE TABLE IF NOT EXISTS pharma_intel_cache (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_name TEXT NOT NULL, source_url TEXT NOT NULL,
  headline TEXT NOT NULL, summary TEXT,
  published_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ DEFAULT now(),
  industry TEXT DEFAULT 'pharma'
);
CREATE INDEX IF NOT EXISTS idx_pharma_intel_fetched 
  ON pharma_intel_cache(industry, fetched_at DESC);

-- category match scoring (Aug 26)
ALTER TABLE audience_contacts ADD COLUMN IF NOT EXISTS category_match_score FLOAT DEFAULT 0.0;
ALTER TABLE audience_contacts ADD COLUMN IF NOT EXISTS match_reasoning TEXT;
```

---

## 16. Common Debugging Patterns

### Staff App not updating on iOS
- **Root cause:** Vercel serves from `public/` but changes made to root files
- **Fix:** Always edit `public/index.html`, `public/sw.js`, `public/vercel.json`
- **Check:** `fetch('/index.html', {cache:'no-store'}).then(r=>r.text()).then(t=>console.log(t.match(/const API = "([^"]+)"/)?.[1]))`

### iOS Safari offline queue not working
- **Root cause 1:** SW cloning POST requests (loses method) → 405 Method Not Allowed
- **Fix:** `if (e.request.method !== 'GET') { e.respondWith(fetch(e.request)...) }`
- **Root cause 2:** Old SW cached in browser
- **Fix:** `caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k))))`

### Railway `context deadline exceeded`
- Occasional snapshot failure — manual redeploy or empty commit

### Supabase `428C9` error
- Writing to generated column `iei_tier` — remove from UPDATE payload

### Modal feature shape mismatch
- Model in volume has different feature count than scorer expects
- Check volume name matches between upload script and scorer app

### Meeting scores all showing 50
- Modal meeting scorer URL not set → fallback to iei_score/2
- Fix: implemented rule-based 5-dimension scorer directly in `meetings.py`

---

## 17. Development History Summary

| Date | Key Deliverables |
|------|-----------------|
| Aug 26, 2026 | Pharma intel cache, enhanced enrichment, category_match_score, XGBoost v4 |
| Aug 28, 2026 | 5-dimension meeting match score, enrichment penalty, Meeting Match filter |
| Sep 1, 2026 | Staff App offline-first (IndexedDB cache, signal queue, iOS SW fix, direct API) |

---

## 18. Next Session Priorities

1. **Staff App `OfferingAssetsViewer`** — read-only asset viewer per offering for staff
2. **Agent tab real data wiring** — replace hardcoded demo with real contact/signal data
3. **8,000 pharma dataset upload** — cost-gated (~$64-72)
4. **Deep IEI loading UI + cancel button**
5. **Zoho chatbot** — "Talk with Us" button integration
6. **XGBoost retrain with real `category_match_score` data** — currently synthetic

---

## 19. match.fingoh.ai Integration

Separate product for bilateral visitor↔exhibitor matching at events.
- Frontend: `fingoh-match` repo
- Backend: `fingoh-match-backend` repo (separate Railway)
- Same Supabase prod DB
- Match score implementation: copy `_compute_match_score()` from `meetings.py`
- Handoff doc: `fingoh_match_score_handoff.md`
- Test event: `b851eebc-206d-4fa5-a3db-ac39a1ea5a4c` (Pharmapack Asia 2026)

---

## 20. Email (Zoho Mail)

| Env | Account ID | From Address |
|-----|-----------|-------------|
| Prod | `5733662000000008002` | `noreply@fingoh.ai` |
| Dev | `670863000000008002` | (broken in dev) |

---

## 21. Known Issues / Parked Items

| Item | Status |
|------|--------|
| Staff App `OfferingAssetsViewer` | Pending — next session |
| Agent tab real data wiring | Parked |
| 8,000 pharma dataset upload | Cost-gated |
| Deep IEI loading UI + cancel button | Parked |
| Zoho chatbot | Not started |
| XGBoost retrain with real data | Future — needs real category_match_score data |
| Electronics & logistics model fine-tuning | Parked |
| `TEST_EMAIL_OVERRIDE` in Railway dev | Must remove before real emails |

---

*Paste this file at the start of every new chat session as the master context document.*
