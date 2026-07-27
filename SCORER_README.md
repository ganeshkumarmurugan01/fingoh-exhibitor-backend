# Fingoh IEI Scorer — Modal Deployment Guide

## Overview

The Fingoh IEI Scorer is a Modal-hosted XGBoost model that scores visitor intent across 41 signals. It runs as a web function endpoint called by the Fingoh backend during audience upload.

---

## Environments

| Environment | Modal App | Modal Volume | Endpoint |
|---|---|---|---|
| **Production** | `fingoh-scorer` | `fingoh-model-vol` | `https://ganeshkumarmurugan01--fingoh-scorer-scorer-score.modal.run` |
| **Development** | `fingoh-scorer-dev` | `fingoh-model-vol-dev` | `https://ganeshkumarmurugan01--fingoh-scorer-dev-scorer-score.modal.run` |

The Railway backend reads `MODAL_SCORER_URL` from its environment variables:
- **Railway dev service** → points to dev scorer endpoint
- **Railway prod service** → points to prod scorer endpoint

---

## Model Files

Both volumes contain the same model files:

| File | Description |
|---|---|
| `iei_model.pkl` | XGBoost regressor — predicts IEI score (0–100) |
| `reg_model.pkl` | XGBoost regressor — predicts registration probability |
| `meeting_model.pkl` | XGBoost classifier — predicts meeting match score |
| `model_version.txt` | Current model version tag |
| `models/` | Additional model artefacts |

---

## Retraining Workflow

**Always retrain on dev first. Never retrain directly on prod.**

```
1. Retrain model locally using retrain_v6.py (or latest retrain script)
2. Upload new model to dev volume only
3. Test scoring on dev environment — validate IEI score distribution
4. If scores look correct, promote to prod (see below)
5. Never skip dev validation
```

### Step 1 — Retrain locally
```bash
cd xgboost-scorer
python retrain_v6.py
```

### Step 2 — Upload to dev volume only
```bash
# Edit upload_models.py to point to fingoh-model-vol-dev
# Change: volume = modal.Volume.from_name("fingoh-model-vol", ...)
# To:     volume = modal.Volume.from_name("fingoh-model-vol-dev", ...)
python upload_models.py
```

### Step 3 — Redeploy dev scorer
```bash
modal deploy scorer_app_dev.py
```

### Step 4 — Validate on dev
- Upload a test CSV on the dev exhibitor app
- Check IEI scores look realistic (T1 ≥ 53, T2 ≥ 43, T3 ≥ 36)
- Compare score distribution across tiers

### Step 5 — Promote to prod
```bash
# Copy dev models to prod volume
modal run copy_models_to_dev.py  # edit script to reverse src/dst first
modal deploy scorer_app.py
```

---

## Tier Thresholds (v6 model)

| Tier | IEI Score | Label |
|---|---|---|
| T1 | ≥ 53 | Hot |
| T2 | ≥ 43 | Warm |
| T3 | ≥ 36 | Cool |
| T4 | < 36 | Cold |

---

## Copying Models Between Environments

Use `copy_models_to_dev.py` to sync models from prod → dev:

```bash
modal run copy_models_to_dev.py
```

To promote dev models to prod, edit the script to swap `/prod` and `/dev` mount paths and volume names.

---

## Files

| File | Purpose |
|---|---|
| `scorer_app.py` | Production Modal app (`fingoh-scorer`) |
| `scorer_app_dev.py` | Development Modal app (`fingoh-scorer-dev`) |
| `retrain_v6.py` | Latest retraining script |
| `upload_models.py` | Upload retrained models to Modal volume |
| `copy_models_to_dev.py` | Copy prod models → dev volume |
| `upload_meeting_model.py` | Upload meeting match model separately |

---

## Critical Rules

- ⚠️ **Never deploy directly to `fingoh-scorer` (prod) without dev validation**
- ⚠️ **Never change `MODAL_SCORER_URL` on Railway prod without testing on dev first**
- ⚠️ **Never run retrain scripts against prod volume directly**
- ✅ All model changes go to `fingoh-scorer-dev` first
- ✅ Prod models are promoted only after dev validation
