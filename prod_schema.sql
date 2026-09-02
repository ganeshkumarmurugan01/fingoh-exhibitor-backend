-- ============================================================
-- Fingoh Platform — Complete Production Schema
-- Run this on a fresh Supabase project to set up a new client instance.
-- All statements use IF NOT EXISTS / OR REPLACE for idempotency.
-- Last updated: Sep 2, 2026
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for text search


-- ============================================================
-- 1. CORE AUTH TABLES (Supabase manages auth.users)
-- ============================================================

CREATE TABLE IF NOT EXISTS profiles (
  id              UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email           TEXT,
  full_name       TEXT,
  org_id          UUID,
  role            TEXT DEFAULT 'owner',
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS organisations (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  TEXT NOT NULL,
  website               TEXT,
  plan                  TEXT DEFAULT 'starter',
  status                TEXT DEFAULT 'active',
  created_at            TIMESTAMPTZ DEFAULT NOW(),
  linkedin_url          TEXT,
  organiser_event_id    UUID,
  is_organiser_managed  BOOLEAN DEFAULT FALSE,
  organiser_powered_label TEXT,
  booth_number          TEXT,
  industry              TEXT,
  company_size          TEXT,
  country               TEXT
);

-- Add FK after both tables exist
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organisations(id);


-- ============================================================
-- 2. PLAN CONFIGS
-- ============================================================

CREATE TABLE IF NOT EXISTS plan_configs (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id                 TEXT UNIQUE NOT NULL,
  name                    TEXT,
  max_events              INT DEFAULT 1,
  max_contacts_per_event  INT DEFAULT 100,
  max_staff               INT DEFAULT 3,
  has_deep_iei            BOOLEAN DEFAULT FALSE,
  has_agent               BOOLEAN DEFAULT FALSE,
  has_crm                 BOOLEAN DEFAULT FALSE,
  has_api_access          BOOLEAN DEFAULT FALSE,
  has_custom_branding     BOOLEAN DEFAULT FALSE,
  has_organiser           BOOLEAN DEFAULT FALSE,
  max_deep_iei            INT DEFAULT 0,
  has_meeting_match       BOOLEAN DEFAULT TRUE,
  has_email_config        BOOLEAN DEFAULT FALSE,
  has_walk_in             BOOLEAN DEFAULT TRUE,
  has_csv_export          BOOLEAN DEFAULT TRUE,
  has_offerings           BOOLEAN DEFAULT TRUE,
  has_product_assets      BOOLEAN DEFAULT FALSE,
  has_pharma_intel        BOOLEAN DEFAULT FALSE,
  price_monthly           NUMERIC DEFAULT 0,
  created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default plans
INSERT INTO plan_configs (plan_id, name, max_events, max_contacts_per_event, max_staff,
  has_deep_iei, has_agent, has_crm, has_meeting_match, has_email_config,
  has_product_assets, has_pharma_intel, max_deep_iei, price_monthly)
VALUES
  ('starter',    'Starter',     1,   200,  3,  FALSE, FALSE, FALSE, TRUE,  FALSE, FALSE, FALSE, 0,   0),
  ('growth',     'Growth',      3,   500,  5,  TRUE,  FALSE, TRUE,  TRUE,  TRUE,  FALSE, FALSE, 50,  99),
  ('scale',      'Scale',       10,  2000, 15, TRUE,  TRUE,  TRUE,  TRUE,  TRUE,  TRUE,  TRUE,  200, 299),
  ('enterprise', 'Enterprise',  999, 9999, 99, TRUE,  TRUE,  TRUE,  TRUE,  TRUE,  TRUE,  TRUE,  999, 999)
ON CONFLICT (plan_id) DO NOTHING;


-- ============================================================
-- 3. PLATFORM CONFIG
-- ============================================================

CREATE TABLE IF NOT EXISTS platform_config (
  key    TEXT PRIMARY KEY,
  value  TEXT
);

INSERT INTO platform_config (key, value) VALUES
  ('maintenance_mode', 'false'),
  ('signup_enabled', 'true')
ON CONFLICT (key) DO NOTHING;


-- ============================================================
-- 4. ADDON CATALOG
-- ============================================================

CREATE TABLE IF NOT EXISTS addon_catalog (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  addon_id        TEXT UNIQUE NOT NULL,
  name            TEXT NOT NULL,
  description     TEXT,
  price_monthly   NUMERIC DEFAULT 0,
  price_onetime   NUMERIC DEFAULT 0,
  feature_key     TEXT,
  quota_key       TEXT,
  quota_amount    INT DEFAULT 0,
  is_active       BOOLEAN DEFAULT TRUE,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS org_addons (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
  addon_id    TEXT NOT NULL,
  quantity    INT DEFAULT 1,
  expires_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- 5. EVENTS
-- ============================================================

CREATE TABLE IF NOT EXISTS events (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
  created_by            UUID REFERENCES profiles(id),
  name                  TEXT NOT NULL,
  venue                 TEXT,
  city                  TEXT,
  country               TEXT,
  date_from             DATE,
  date_to               DATE,
  status                TEXT DEFAULT 'pre_event',
  industry_vertical     TEXT DEFAULT 'general',
  company_name          TEXT,
  company_website       TEXT,
  company_linkedin      TEXT,
  booth_number          TEXT,
  product_service       TEXT,
  iei_credits           INT DEFAULT 100,
  enrichment_paused     BOOLEAN DEFAULT FALSE,
  previous_event_id     UUID REFERENCES events(id),
  organiser_event_id    UUID,
  logo_url              TEXT,
  banner_url            TEXT,
  linkedin_url          TEXT,
  created_at            TIMESTAMPTZ DEFAULT NOW(),
  updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_categories (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id    UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  category    TEXT NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_icp (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id        UUID UNIQUE NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  roles           JSONB DEFAULT '[]',
  company_sizes   JSONB DEFAULT '[]',
  visit_reasons   JSONB DEFAULT '[]',
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_intent (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id        UUID UNIQUE NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  why             TEXT,
  buyers          TEXT,
  signals         TEXT,
  buyer_signals   TEXT,
  intent_signals  JSONB DEFAULT '[]',
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_offerings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
  event_id        UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  description     TEXT,
  category        TEXT,
  tags            JSONB DEFAULT '[]',
  is_active       BOOLEAN DEFAULT TRUE,
  category_master JSONB DEFAULT '[]',
  sort_order      INT DEFAULT 0,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS offering_assets (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  offering_id     UUID NOT NULL REFERENCES event_offerings(id) ON DELETE CASCADE,
  event_id        UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  org_id          UUID NOT NULL,
  asset_type      TEXT NOT NULL CHECK (asset_type IN ('photo','video','brochure')),
  file_name       TEXT NOT NULL,
  storage_path    TEXT NOT NULL,
  public_url      TEXT,
  file_size_bytes BIGINT,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS email_config (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
  event_id              UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  from_name             TEXT,
  from_email            TEXT,
  reply_to              TEXT,
  logo_url              TEXT,
  banner_url            TEXT,
  primary_color         TEXT DEFAULT '#26215C',
  secondary_color       TEXT DEFAULT '#4338A0',
  footer_text           TEXT,
  meeting_subject       TEXT,
  meeting_body_template TEXT,
  accept_template       TEXT,
  decline_template      TEXT,
  reminder_template     TEXT,
  custom_css            TEXT,
  created_at            TIMESTAMPTZ DEFAULT NOW(),
  updated_at            TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(org_id, event_id)
);


-- ============================================================
-- 6. AUDIENCE CONTACTS
-- ============================================================

CREATE TABLE IF NOT EXISTS audience_contacts (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id              UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  name                  TEXT,
  email                 TEXT,
  designation           TEXT,
  company               TEXT,
  country               TEXT,
  city                  TEXT,
  phone                 TEXT,
  linkedin_url          TEXT,
  company_size          TEXT,
  raw_data              JSONB DEFAULT '{}',
  iei_score             FLOAT DEFAULT 43.0,
  iei_tier              TEXT GENERATED ALWAYS AS (
                          CASE
                            WHEN iei_score >= 62 THEN 'T1'
                            WHEN iei_score >= 44 THEN 'T2'
                            WHEN iei_score >= 34 THEN 'T3'
                            ELSE 'T4'
                          END
                        ) STORED,
  reg_prob              FLOAT DEFAULT 0.43,
  icp_fit_score         FLOAT DEFAULT 0.0,
  enrichment_status     TEXT DEFAULT 'pending',
  iei_research          JSONB,
  meeting_match_analysis JSONB,
  meeting_match_analysed_at TIMESTAMPTZ,
  onsite_iei_score      FLOAT,
  onsite_iei_tier       TEXT,
  prev_iei_score        FLOAT,
  source                TEXT DEFAULT 'csv',
  category_match_score  FLOAT DEFAULT 0.0,
  match_reasoning       TEXT,
  created_at            TIMESTAMPTZ DEFAULT NOW(),
  updated_at            TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(event_id, email)
);

CREATE INDEX IF NOT EXISTS idx_audience_contacts_event
  ON audience_contacts(event_id, iei_score DESC);
CREATE INDEX IF NOT EXISTS idx_audience_contacts_enrichment
  ON audience_contacts(event_id, enrichment_status);


-- ============================================================
-- 7. CONVERSATION SIGNALS (Staff App)
-- ============================================================

CREATE TABLE IF NOT EXISTS conversation_signals (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id            UUID NOT NULL REFERENCES audience_contacts(id) ON DELETE CASCADE,
  event_id              UUID,
  conversation_quality  INT CHECK (conversation_quality BETWEEN 1 AND 5),
  question_types        JSONB DEFAULT '[]',
  questions_type        TEXT,
  demo_requested        BOOLEAN DEFAULT FALSE,
  demo_attendance       BOOLEAN DEFAULT FALSE,
  badge_scan            BOOLEAN DEFAULT FALSE,
  buying_group          BOOLEAN DEFAULT FALSE,
  return_visit          BOOLEAN DEFAULT FALSE,
  collateral            TEXT DEFAULT 'none',
  meeting_booked        BOOLEAN DEFAULT FALSE,
  urgency               TEXT DEFAULT 'low',
  notes                 TEXT,
  voice_transcript      TEXT,
  ai_intent_level       TEXT,
  ai_buying_signals     JSONB DEFAULT '[]',
  ai_red_flags          JSONB DEFAULT '[]',
  ai_score_delta        FLOAT,
  ai_recommended_action TEXT,
  logged_by             TEXT,
  logged_at             TIMESTAMPTZ DEFAULT NOW(),
  staff_name            TEXT,
  created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_signals_contact
  ON conversation_signals(contact_id, created_at DESC);


-- ============================================================
-- 8. MEETING REQUESTS
-- ============================================================

CREATE TABLE IF NOT EXISTS meeting_requests (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id          UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  contact_id        UUID NOT NULL REFERENCES audience_contacts(id) ON DELETE CASCADE,
  status            TEXT DEFAULT 'pending',
  proposed_datetime TIMESTAMPTZ,
  confirmed_datetime TIMESTAMPTZ,
  duration_minutes  INT DEFAULT 30,
  location          TEXT,
  topic             TEXT,
  notes             TEXT,
  requested_at      TIMESTAMPTZ DEFAULT NOW(),
  responded_at      TIMESTAMPTZ,
  match_score       FLOAT,
  meeting_prob      FLOAT,
  start_time        TIMESTAMPTZ,
  end_time          TIMESTAMPTZ,
  meeting_notes     TEXT,
  voice_transcript  TEXT,
  ai_analysis       JSONB,
  staff_name        TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meeting_tokens (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  meeting_id  UUID NOT NULL REFERENCES meeting_requests(id) ON DELETE CASCADE,
  token       TEXT UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'),
  action      TEXT NOT NULL,
  used        BOOLEAN DEFAULT FALSE,
  expires_at  TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '7 days'),
  created_at  TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- 9. STAFF
-- ============================================================

CREATE TABLE IF NOT EXISTS staff (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  email       TEXT NOT NULL,
  role        TEXT DEFAULT 'staff',
  passcode    TEXT DEFAULT '1234',
  is_active   BOOLEAN DEFAULT TRUE,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(org_id, email)
);


-- ============================================================
-- 10. CRM CONNECTIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS crm_connections (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
  crm_type        TEXT NOT NULL,
  access_token    TEXT,
  refresh_token   TEXT,
  token_expires_at TIMESTAMPTZ,
  instance_url    TEXT,
  connected_at    TIMESTAMPTZ DEFAULT NOW(),
  last_sync_at    TIMESTAMPTZ,
  is_active       BOOLEAN DEFAULT TRUE,
  metadata        JSONB DEFAULT '{}',
  created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- 11. AGENT OUTPUTS
-- ============================================================

CREATE TABLE IF NOT EXISTS agent_outputs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id        UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  contact_id      UUID REFERENCES audience_contacts(id) ON DELETE SET NULL,
  output_type     TEXT NOT NULL,
  subject         TEXT,
  body            TEXT,
  metadata        JSONB DEFAULT '{}',
  status          TEXT DEFAULT 'draft',
  sent_at         TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- 12. ACTIVITY LOGS
-- ============================================================

CREATE TABLE IF NOT EXISTS activity_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID,
  user_id     UUID,
  action      TEXT NOT NULL,
  entity_type TEXT,
  entity_id   UUID,
  metadata    JSONB DEFAULT '{}',
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activity_logs_org
  ON activity_logs(org_id, created_at DESC);


-- ============================================================
-- 13. CATEGORY MASTER (Pharma taxonomy)
-- ============================================================

CREATE TABLE IF NOT EXISTS category_master (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  industry    TEXT NOT NULL DEFAULT 'pharma',
  level       INT NOT NULL,
  code        TEXT,
  name        TEXT NOT NULL,
  parent_id   UUID REFERENCES category_master(id),
  description TEXT,
  is_custom   BOOLEAN DEFAULT FALSE,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_category_master_industry
  ON category_master(industry, level, parent_id);


-- ============================================================
-- 14. PHARMA INTEL CACHE
-- ============================================================

CREATE TABLE IF NOT EXISTS pharma_intel_cache (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_name  TEXT NOT NULL,
  source_url   TEXT NOT NULL,
  headline     TEXT NOT NULL,
  summary      TEXT,
  published_at TIMESTAMPTZ,
  fetched_at   TIMESTAMPTZ DEFAULT NOW(),
  industry     TEXT DEFAULT 'pharma'
);

CREATE INDEX IF NOT EXISTS idx_pharma_intel_fetched
  ON pharma_intel_cache(industry, fetched_at DESC);


-- ============================================================
-- 15. ORGANISER MODULE
-- ============================================================

CREATE TABLE IF NOT EXISTS organisers (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  contact_email   TEXT,
  contact_name    TEXT,
  website         TEXT,
  status          TEXT DEFAULT 'active',
  data_used       INT DEFAULT 0,
  plan            TEXT DEFAULT 'basic',
  notes           TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS organiser_users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organiser_id  UUID NOT NULL REFERENCES organisers(id) ON DELETE CASCADE,
  email         TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  full_name     TEXT,
  role          TEXT DEFAULT 'admin',
  last_login    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(email)
);

CREATE TABLE IF NOT EXISTS organiser_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organiser_id  UUID NOT NULL REFERENCES organisers(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  venue         TEXT,
  city          TEXT,
  country       TEXT,
  date_from     DATE,
  date_to       DATE,
  status        TEXT DEFAULT 'active',
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Add FK from events and organisations to organiser_events
ALTER TABLE events
  ADD CONSTRAINT IF NOT EXISTS fk_events_organiser_event
  FOREIGN KEY (organiser_event_id) REFERENCES organiser_events(id);

ALTER TABLE organisations
  ADD CONSTRAINT IF NOT EXISTS fk_orgs_organiser_event
  FOREIGN KEY (organiser_event_id) REFERENCES organiser_events(id);

CREATE TABLE IF NOT EXISTS organiser_exhibitor_links (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organiser_id          UUID NOT NULL REFERENCES organisers(id) ON DELETE CASCADE,
  organiser_event_id    UUID NOT NULL REFERENCES organiser_events(id) ON DELETE CASCADE,
  exhibitor_id          UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
  status                TEXT DEFAULT 'invited',
  invite_token          TEXT UNIQUE DEFAULT encode(gen_random_bytes(16), 'hex'),
  data_allocation       INT DEFAULT 50,
  data_consumed         INT DEFAULT 0,
  invited_at            TIMESTAMPTZ DEFAULT NOW(),
  accepted_at           TIMESTAMPTZ,
  created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS organiser_visitor_uploads (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organiser_event_id  UUID NOT NULL REFERENCES organiser_events(id) ON DELETE CASCADE,
  file_name           TEXT,
  row_count           INT DEFAULT 0,
  status              TEXT DEFAULT 'processing',
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS organiser_visitor_rows (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organiser_event_id  UUID NOT NULL REFERENCES organiser_events(id) ON DELETE CASCADE,
  upload_id           UUID REFERENCES organiser_visitor_uploads(id),
  raw_data            JSONB DEFAULT '{}',
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS organiser_import_log (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organiser_event_id  UUID NOT NULL REFERENCES organiser_events(id) ON DELETE CASCADE,
  link_id             UUID REFERENCES organiser_exhibitor_links(id),
  exhibitor_id        UUID REFERENCES organisations(id),
  rows_imported       INT DEFAULT 0,
  imported_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_organiser_visitor_rows_event
  ON organiser_visitor_rows(organiser_event_id, created_at DESC);


-- ============================================================
-- 16. ROW LEVEL SECURITY (RLS)
-- ============================================================

-- Enable RLS on key tables
ALTER TABLE profiles             ENABLE ROW LEVEL SECURITY;
ALTER TABLE organisations        ENABLE ROW LEVEL SECURITY;
ALTER TABLE events               ENABLE ROW LEVEL SECURITY;
ALTER TABLE audience_contacts    ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_requests     ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_offerings      ENABLE ROW LEVEL SECURITY;
ALTER TABLE offering_assets      ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff                ENABLE ROW LEVEL SECURITY;

-- Profiles: users can read/update their own profile
CREATE POLICY IF NOT EXISTS "profiles_own" ON profiles
  FOR ALL USING (auth.uid() = id);

-- Organisations: members can read their org
CREATE POLICY IF NOT EXISTS "orgs_member" ON organisations
  FOR SELECT USING (
    id IN (SELECT org_id FROM profiles WHERE id = auth.uid())
  );

-- Service role bypass (for backend)
CREATE POLICY IF NOT EXISTS "service_role_bypass_profiles" ON profiles
  FOR ALL TO service_role USING (true);
CREATE POLICY IF NOT EXISTS "service_role_bypass_orgs" ON organisations
  FOR ALL TO service_role USING (true);
CREATE POLICY IF NOT EXISTS "service_role_bypass_events" ON events
  FOR ALL TO service_role USING (true);
CREATE POLICY IF NOT EXISTS "service_role_bypass_contacts" ON audience_contacts
  FOR ALL TO service_role USING (true);
CREATE POLICY IF NOT EXISTS "service_role_bypass_signals" ON conversation_signals
  FOR ALL TO service_role USING (true);
CREATE POLICY IF NOT EXISTS "service_role_bypass_meetings" ON meeting_requests
  FOR ALL TO service_role USING (true);
CREATE POLICY IF NOT EXISTS "service_role_bypass_offerings" ON event_offerings
  FOR ALL TO service_role USING (true);
CREATE POLICY IF NOT EXISTS "service_role_bypass_assets" ON offering_assets
  FOR ALL TO service_role USING (true);
CREATE POLICY IF NOT EXISTS "service_role_bypass_staff" ON staff
  FOR ALL TO service_role USING (true);


-- ============================================================
-- 17. STORAGE BUCKETS
-- ============================================================

-- Note: Run these in Supabase dashboard Storage section, or via API
-- Buckets needed:
--   product-assets  (private, for offering photos/video/brochure)
--   email-assets    (public, for email logos/banners)

-- ============================================================
-- DONE
-- ============================================================
-- After running this schema:
-- 1. Create storage buckets: product-assets (private), email-assets (public)
-- 2. Set SUPABASE_URL + SUPABASE_SERVICE_KEY in Railway env vars
-- 3. Set SUPABASE_URL + SUPABASE_ANON_KEY in Vercel env vars
-- 4. Seed pharma category_master if needed (run seed_pharma_categories.sql)
-- 5. Seed plan_configs (already done above)
-- ============================================================
