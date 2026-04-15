# Meesho Campus Decline Predictor — Backend

## What this is
A FastAPI backend that:
1. Reads your live Google Sheet (no API key needed)
2. Scores all 245 candidates using the signal weights
3. Exposes a REST API that the HTML frontend calls

## Files
- `main.py` — all backend logic
- `requirements.txt` — Python dependencies
- `render.yaml` — Render.com deployment config
- `weights.json` — current signal weights (editable via the UI)
- `outcomes.csv` — recorded join/decline outcomes for future model training

## Deploy to Render (step by step)

### Step 1 — Put these files on GitHub
1. Go to github.com → sign in (or create free account)
2. Click **+** (top right) → **New repository**
3. Name it `meesho-predictor-backend` → click **Create repository**
4. Upload all 5 files from this folder (drag and drop on the GitHub page)
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
https://meesho-predictor-backend.onrender.com/score
```
You should see JSON with all candidates scored.

### Step 4 — Connect the HTML app
In the HTML app, set the Backend URL field to:
```
https://meesho-predictor-backend.onrender.com
```
The app will now pull live data from your sheet instead of using baked-in data.

## API Endpoints

| Method | Endpoint | What it does |
|--------|----------|--------------|
| GET | `/score` | Score all candidates from live sheet |
| GET | `/weights` | Return current weights |
| POST | `/weights` | Update weights + return re-scored list |
| POST | `/outcome` | Record a join/decline result |
| GET | `/outcomes` | Return all recorded outcomes |
| GET | `/health` | Health check |

## Column name mapping
If your sheet column headers are named differently, edit `COL_MAP` in `main.py`.
Current mappings expect headers like:
- "Candidate Name", "College", "Role", "CGPA", "DOJ"
- "Google form - Joining Dates", "Google form - SWAG"
- "Gmeet - Kick off", "Gmeet - AMA"
- "LI Profile - Meesho", "LI Post - Liked and Commented", etc.
- "Calling data"

## Note on free tier
Render's free tier spins down after 15 minutes of inactivity.
The first request after a spin-down takes ~30 seconds to wake up.
Subsequent requests are fast. This is fine for internal HR tooling.
