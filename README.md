# Meesho Campus Decline Predictor — Backend

## What this is
A FastAPI backend that:
1. Reads your live Google Sheet (no API key needed)
2. Scores candidates using explainable components and calibrated signal weights
3. Groups candidates into High, Watch, Low, and Confirmed Decline queues
4. Exposes a REST API that the HTML frontend calls

## Files
- `main.py` — all backend logic
- `index.html` — standalone dashboard UI
- `requirements.txt` — Python dependencies
- `render.yaml` — Render.com deployment config
- `weights.json` — current signal weights, including engagement sliders
- `outcomes.csv` — local fallback join/decline outcomes when the sheet has no outcome

## Required environment variables

| Variable | Purpose |
|----------|---------|
| `SHEET_ID` | Google Sheet ID. Defaults to the current tracker ID. |
| `SHEET_NAME` | Sheet tab name. Defaults to `Main Tracker`. |

## Deploy to Render (step by step)

### Step 1 — Put these files on GitHub
1. Go to github.com → sign in (or create free account)
2. Click **+** (top right) → **New repository**
3. Name it `meesho-predictor-backend` → click **Create repository**
4. Upload the project files from this folder (drag and drop on the GitHub page)
5. Click **Commit changes**

### Step 2 — Deploy on Render
1. Go to render.com → sign in with GitHub
2. Click **New +** → **Web Service**
3. Click **Connect** next to your `meesho-predictor-backend` repo
4. Render auto-detects everything from `render.yaml`
5. Click **Create Web Service**
6. Wait ~2 minutes for it to build
7. Your API is live at: `https://meesho-predictor-backend.onrender.com`

### Step 3 — Test it
Open in browser:
```
https://meesho-predictor-backend.onrender.com/health
```
You should see JSON with `"status": "healthy"`.

### Step 4 — Open the dashboard
The bundled HTML app is served from:
```
https://meesho-predictor-backend.onrender.com/app
```
The app will pull live data from your sheet.

## API Endpoints

| Method | Endpoint | What it does |
|--------|----------|--------------|
| GET | `/app` | Serve the dashboard UI |
| GET | `/score` | Score all candidates from live sheet |
| GET | `/weights` | Return current weights |
| POST | `/weights` | Validate and save updated weights |
| POST | `/weights/save` | Validate and save updated weights |
| POST | `/outcome` | Record a local fallback join/decline result |
| GET | `/outcomes` | Return local fallback outcomes |
| GET | `/health` | Health check |

## Scoring model

The score is a 0-100 recruiter triage score, not a literal probability. Each
candidate receives component scores for:
- `market` — college tier, role, CGPA, internship duration, and internship company
- `engagement` — joining form, SWAG form, GMeet attendance, LinkedIn activity, and call sentiment
- `urgency` — May and June DOJ timing
- `call_notes` — risk or reassurance from recruiter call remarks
- `follow_up` — near-term DOJ candidates without a recorded recruiter call

The API also returns `category`, `category_label`, `components`, and `reasons`
for each candidate so the dashboard can explain why someone is in High or Watch.

The default buckets are intentionally conservative:
- `High` is the immediate intervention queue. It requires a score of 70+, a
  critical-engagement score of 65+, or a hard negative call signal.
- `Watch` captures candidates with meaningful supporting evidence, including
  medium scores, critical engagement below the High cutoff, and near-term May
  DOJ candidates without a recorded recruiter call.
- `Low` means no current action signal beyond normal cohort follow-up.

## Column name mapping
If your sheet column headers are named differently, edit `COL_MAP` in `main.py`.
Current mappings expect headers like:
- "Name of the Candidate", "College Name", "Role offered", "CGPA", "DOJ"
- "Google form - Joining Dates", "Google form - SWAG"
- "Gmeet 1 - Kick off attendance", "Gmeet - AMA"
- "LI Profile mentions Meesho?", "LI Post 4 - Meesho Day Zero", etc.
- "Call Remarks"

## Persistence note

`weights.json` and `outcomes.csv` are written atomically and validated, but Render's free web service filesystem is not a durable database. For permanent multi-user history, move these to a managed store such as Postgres, Redis, or a protected Google Sheet tab.

## Note on free tier
Render's free tier spins down after 15 minutes of inactivity.
The first request after a spin-down takes ~30 seconds to wake up.
Subsequent requests are fast. This is fine for internal HR tooling.
