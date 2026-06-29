# Fingoh IEI Framework — 41 Signal Library

## ML Models Used
1. Pre-reg prediction (CSV upload → predicted registrations): XGBoost on Modal (fingoh-scorer)
2. Attendance prediction (pre-reg → who attends): Cox Proportional Hazards model
3. Meeting intent (attendees → serious buyers): LambdaMART
4. Pipeline calculation: Monte Carlo simulation
5. Onsite rescore: XGBoost (same Modal model) with onsite signals overlaid, 70% onsite / 30% firmographic

## Signal Weights
- Weight 10 (Critical): ICP fit score, Meeting requests sent, Conversation quality score, Return visits, Proposal/demo request
- Weight 9 (Critical): Job title/seniority, Meeting acceptance rate, Questions type, Buying cycle stage, Follow-up email response, CRM stage progression
- Weight 8 (Strong): Profile completeness, Company size/sector, App sessions, Booth dwell time, Trigger events, Competitive displacement, Website visit post-event
- Weight 7 (Strong): Registration timing, Interest categories, Session registration, Content downloads, Session attendance ratio, Previous event history
- Weight 6 (Moderate): Badge scan, Email open/click rate, Pre-event content engagement, ROI report
- Weight 5 (Weak): Social mentions, Collateral requested, Social amplification

## During-Event On-Site Signals (ground truth — highest weight)
- Badge scan count: Weight 6, Low reliability
- Booth dwell time: Weight 8, Medium reliability
- Demo/presentation attendance: Weight 9, High reliability
- Return visits to same booth: Weight 10, Very High reliability
- Session attendance ratio: Weight 7, High reliability
- Conversation quality score (1-5): Weight 10, Very High reliability
- Questions asked type: Weight 9, High reliability
- Collateral requested: Weight 5, Low reliability

## Currently Captured in Staff App
- Conversation quality (1-5): Weight 10
- Questions type (pricing/implementation/technical/competitive/general): Weight 9
- Return visit toggle: Weight 10
- Demo attendance toggle: Weight 9
- Badge scan toggle: Weight 6
- Buying group present: Weight 8 (competitive displacement proxy)
- Meeting booked: Weight 10 (proposal/demo request proxy)
- Collateral (none/generic/specific): Weight 5
- Urgency (low/medium/high): Weight 9 (buying cycle stage proxy)
- Notes + Voice + AI analysis: qualitative enrichment

## Signals NOT Yet Captured (future roadmap)
- Booth dwell time (needs RFID/beacons): Weight 8
- Session attendance ratio (needs event platform): Weight 7
- Matchmaking engagement (needs platform integration): Weight 8
- App session frequency (needs event app): Weight 8
- Microsite visits (needs analytics): Weight 9

## Scoring Philosophy
- Onsite signals are ground truth — override pre-event signals
- Score hits 90+ only when multiple Very High reliability signals fire together
- Pre-event engagement signals discounted 80% during onsite rescoring
- Post-event signals zeroed during onsite rescoring
- ICP fit is a multiplier — weak signal from perfect ICP > strong signal from non-ICP

## Architecture
- Modal scorer URL: https://ganeshkumarmurugan01--fingoh-scorer-scorer-score.modal.run
- Modal health URL: https://ganeshkumarmurugan01--fingoh-scorer-scorer-health.modal.run
- Model files: ~/fingoh-exhibitor-root/xgboost-scorer/ (iei_model.pkl, reg_model.pkl)
- Onsite blend: 70% conv_quality_score x 100 + 30% XGBoost firmographic + signal bonus (capped +8/-5)

## Stack
- Frontend: React/Vite on Vercel (fingoh-exhibitor.vercel.app)
- Staff App: HTML on Vercel (fingoh-staff.vercel.app)
- Backend: FastAPI on Railway
- DB: Supabase (audience_contacts, conversation_signals tables)
- ML: Modal (XGBoost IEI + reg_prob models)
- Realtime: Supabase Realtime on conversation_signals + audience_contacts
