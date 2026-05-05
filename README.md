# Meesho Campus Decline Predictor — Backend

## What this is
A FastAPI backend that:
1. Reads your live Google Sheet (no API key needed)
2. Scores all 245 candidates using the signal weights
3. Exposes an authenticated REST API that the HTML frontend calls

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
| `PREDICTOR_API_KEY` | Shared secret required in the `X-Predictor-Key` header for `/score`, `/weights`, and `/outcome` |
| `ALLOWED_ORIGINS` | Comma-separated frontend origins allowed by CORS, e.g. `https://your-dashboard.example.com` |
| `SHEET_ID` | Google Sheet ID. Defaults to the current tracker ID. |
| `SHEET_NAME` | Sheet tab name. Defaults to `Main Tracker`. |

If you open `index.html` directly from disk during local testing, the browser origin is `null`; include `null` in `ALLOWED_ORIGINS` only for that local/internal workflow.

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
5. Add a secret value for `PREDICTOR_API_KEY`
6. Set `ALLOWED_ORIGINS` to the exact URL where the dashboard will be opened
7. Click **Create Web Service**
8. Wait ~2 minutes for it to build
9. Your API is live at: `https://meesho-predictor-backend.onrender.com`

### Step 3 — Test it
Open in browser:
```
https://meesho-predictor-backend.onrender.com/health
```
You should see JSON with `"auth_configured": true`.

To test `/score`, send the access key:
```
curl -H "X-Predictor-Key: YOUR_KEY" https://meesho-predictor-backend.onrender.com/score
```

### Step 4 — Open the dashboard
The bundled HTML app is served from:
```
https://meesho-predictor-backend.onrender.com/app
```
Enter the same backend access key in the dashboard's Data Source bar. The app will now pull live data from your sheet.

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

All endpoints except `/`, `/app`, and `/health` require:
```
X-Predictor-Key: YOUR_KEY
```

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

