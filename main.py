"""
Meesho Campus Decline Predictor — FastAPI Backend
Reads live data from Google Sheets, scores candidates, exposes REST API.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import requests
import json
import os
import csv
from datetime import datetime
from typing import Optional

app = FastAPI(title="Meesho Campus Predictor API")

# Allow requests from any frontend (the HTML app, localhost, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── CONFIG ────────────────────────────────────────────────────────────────────

SHEET_ID = "1SXojteQ8RpbEucxXbPRd0maLlasebibw4eVbNmNqdgI"
WEIGHTS_FILE = "weights.json"
OUTCOMES_FILE = "outcomes.csv"

# Column name mappings from your Google Sheet headers
# Edit these if your sheet column names differ
COL_MAP = {
    "name":          "Name of the Candidate",
    "college":       "College Name",
    "role":          "Role offered",
    "cgpa":          "CGPA",
    "doj":           "DOJ",
    "joining_form":  "Google form - Joining Dates",
    "swag_form":     "Google form - SWAG",
    "gmeet_k":       "Gmeet 1 - Kick off attendance",
    "gmeet_a":       "Gmeet - AMA",
    "li_mention":    "LI Profile mentions Meesho?",
    "li_lc":         "LI Post 4 - Meesho Day Zero",
    "li_c":          "LI Post 2 - Introduction",
    "li_l":          "LI Post 6 - Founder's letter Poll",
    "intern_months": "Type of Internship",
    "intern_company":"Internship company",
    "calling_data":  "Call Remarks",
}

# Tier-1 internship companies (PPO / competing offer risk)
TIER1_COMPANIES = [
    "google", "microsoft", "amazon", "meta", "apple", "goldman", "morgan stanley",
    "mckinsey", "bcg", "bain", "deloitte", "jp morgan", "jpmorgan", "blackrock",
    "citadel", "jane street", "d.e. shaw", "two sigma", "tower research",
    "trexquant", "worldquant", "optiver", "de shaw", "adobe", "salesforce",
    "uber", "airbnb", "stripe", "atlassian", "linkedin",
]

# Default weights (used if weights.json doesn't exist yet)
DEFAULT_WEIGHTS = {
    "tier1": 18, "tier2": 10, "other": 5,
    "tech": 10,
    "cgpa_high": 14, "cgpa_mid": 7, "cgpa_low": 3,
    "intern6m": 10, "intern_tier1": 10,
    "eng_critical": 30, "eng_risky": 14, "eng_safe": -15,
    "threshold": 65
}

# ── WEIGHTS PERSISTENCE ───────────────────────────────────────────────────────

def load_weights() -> dict:
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE) as f:
            return json.load(f)
    return DEFAULT_WEIGHTS.copy()

def save_weights(w: dict):
    with open(WEIGHTS_FILE, "w") as f:
        json.dump(w, f, indent=2)

# ── GOOGLE SHEETS READER ──────────────────────────────────────────────────────

def fetch_sheet_data() -> list[dict]:
    """
    Reads the Google Sheet as CSV (no API key needed — sheet must be public viewer).
    Returns a list of row dicts with raw string values.
    """
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet=Main%20Tracker"
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Could not reach Google Sheet (status {resp.status_code}). Make sure it is set to 'Anyone with link can view'.")
    
    lines = resp.text.splitlines()
    reader = csv.DictReader(lines)
    return [row for row in reader]

# ── CALL NOTE CLASSIFIER ──────────────────────────────────────────────────────

def classify_call_note(note: str) -> int:
    """Returns a call_risk integer from free-text recruiter notes."""
    if not note:
        return 0
    n = note.lower()
    if any(k in n for k in ["dream company", "no red flag", "definitely joining", "very excited"]):
        return 5
    if any(k in n for k in ["excited", "keen", "looking forward", "confirmed"]):
        return 3
    if any(k in n for k in ["not reachable", "not picking up", "multiple attempts", "no answer", "ghosting"]):
        return -6
    if any(k in n for k in ["mba", "masters", "phd", "higher studies", "ms admit"]):
        return -4
    if any(k in n for k in ["ppo", "competing offer", "another offer", "other company", "placed elsewhere"]):
        return -3
    if any(k in n for k in ["risky", "red flag", "concerned", "might not join", "thinking"]):
        return -2
    return 0

# ── FIELD PARSERS ─────────────────────────────────────────────────────────────

def parse_bool(val: str) -> int:
    if not val:
        return 0
    v = val.strip().lower()
    return 1 if v in ["yes", "true", "1", "✓", "done", "filled", "attended", "y"] else 0

def parse_cgpa(val: str) -> float:
    try:
        return float(str(val).strip())
    except:
        return 7.5  # default mid-band

def parse_college_tier(college: str) -> int:
    c = college.lower()
    if any(k in c for k in ["iit", "iisc", "iiit hyderabad", "bits"]):
        return 1
    if any(k in c for k in ["nit", "iiit", "vit", "srm", "manipal"]):
        return 2
    return 3

def parse_intern_months(val: str) -> int:
    v = val.lower() if val else ""
    return 6 if "6m" in v or "winter" in v or "6 month" in v else 2

def parse_intern_tier(company: str) -> int:
    if not company:
        return 0
    c = company.lower()
    return 1 if any(k in c for k in TIER1_COMPANIES) else 0

def parse_doj_month(doj: str) -> str:
    if not doj:
        return "Jul"
    d = doj.lower()
    if "may" in d: return "May"
    if "jun" in d: return "Jun"
    return "Jul"

def parse_role(role: str) -> str:
    r = role.upper() if role else ""
    if "SDE" in r or "SOFTWARE" in r: return "SDE-I"
    if "DS" in r or "DATA SCIEN" in r: return "DS-I"
    if "MLE" in r or "ML ENGIN" in r: return "MLE-I"
    if "OR" in r or "OPERATIONS RES" in r: return "OR-I"
    if "SA" in r or "BUSINESS" in r: return "SA-BMT"
    return role or "Other"

# ── SCORING ENGINE ────────────────────────────────────────────────────────────

def engagement_score(c: dict) -> float:
    eng = 0.0
    eng += 4 if c["joining_form"] else -2
    eng += 1 if c["swag_form"] else -1
    eng += 2 if c["gmeet_k"] else -0.5
    eng += 2 if c["gmeet_a"] else -0.5
    if c["li_mention"]: eng += 3
    if c["li_lc"]: eng += 2
    if c["li_c"]: eng += 1.5
    if c["li_l"]: eng += 1
    if c["intern_months"] == 6: eng -= 2
    if c["intern_tier"] == 1: eng -= 2
    eng += c["call_risk"]
    return round(eng, 1)

def engagement_label(score: float) -> str:
    if score < 4: return "critical"
    if score < 10: return "risky"
    return "safe"

def calc_risk(c: dict, weights: dict) -> dict:
    pts = 0
    if c["tier"] == 1: pts += weights["tier1"]
    elif c["tier"] == 2: pts += weights["tier2"]
    else: pts += weights["other"]

    if c["role"] in ["SDE-I", "DS-I", "MLE-I", "OR-I"]:
        pts += weights["tech"]

    if c["cgpa"] >= 8.5: pts += weights["cgpa_high"]
    elif c["cgpa"] >= 7.0: pts += weights["cgpa_mid"]
    else: pts += weights["cgpa_low"]

    if c["intern_months"] == 6: pts += weights["intern6m"]
    if c["intern_tier"] == 1: pts += weights["intern_tier1"]

    eng = engagement_score(c)
    label = engagement_label(eng)
    if label == "critical": pts += weights["eng_critical"]
    elif label == "risky": pts += weights["eng_risky"]
    else: pts += weights["eng_safe"]

    return {
        "risk_pct": min(max(int(pts), 5), 100),
        "eng_score": eng,
        "eng_label": label,
    }

# ── OUTCOMES PERSISTENCE ──────────────────────────────────────────────────────

def load_outcomes() -> dict:
    outcomes = {}
    # Pre-load confirmed declines from this cohort
    for name in ["Lakshya Jain", "Aakash Kumar Singh", "Arvind Vidhyashankar"]:
        outcomes[name] = "declined"
    if os.path.exists(OUTCOMES_FILE):
        with open(OUTCOMES_FILE) as f:
            for line in f:
                parts = line.strip().split(",", 1)
                if len(parts) == 2:
                    outcomes[parts[0]] = parts[1]
    return outcomes

def save_outcome(name: str, outcome: str):
    with open(OUTCOMES_FILE, "a") as f:
        f.write(f"{name},{outcome},{datetime.now().isoformat()}\n")

# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

def build_candidates(raw_rows: list[dict], weights: dict) -> list[dict]:
    """Parses raw sheet rows → scores each candidate → returns sorted list."""
    outcomes = load_outcomes()
    candidates = []

    for row in raw_rows:
        name = row.get(COL_MAP["name"], "").strip()
        if not name:
            continue  # skip blank rows

        college = row.get(COL_MAP["college"], "")
        role_raw = row.get(COL_MAP["role"], "")
        call_note = row.get(COL_MAP["calling_data"], "")

        c = {
            "name": name,
            "college": college,
            "role": parse_role(role_raw),
            "cgpa": parse_cgpa(row.get(COL_MAP["cgpa"], "7.5")),
            "tier": parse_college_tier(college),
            "doj": parse_doj_month(row.get(COL_MAP["doj"], "")),
            "joining_form": parse_bool(row.get(COL_MAP["joining_form"], "")),
            "swag_form": parse_bool(row.get(COL_MAP["swag_form"], "")),
            "gmeet_k": parse_bool(row.get(COL_MAP["gmeet_k"], "")),
            "gmeet_a": parse_bool(row.get(COL_MAP["gmeet_a"], "")),
            "li_mention": parse_bool(row.get(COL_MAP["li_mention"], "")),
            "li_lc": parse_bool(row.get(COL_MAP["li_lc"], "")),
            "li_c": parse_bool(row.get(COL_MAP["li_c"], "")),
            "li_l": parse_bool(row.get(COL_MAP["li_l"], "")),
            "intern_months": parse_intern_months(row.get(COL_MAP["intern_months"], "")),
            "intern_tier": parse_intern_tier(row.get(COL_MAP["intern_company"], "")),
            "call_risk": classify_call_note(call_note),
            "call_note": call_note[:200] if call_note else "",
            "called": bool(call_note and call_note.strip()),
            "outcome": outcomes.get(name),
        }

        scores = calc_risk(c, weights)
        candidates.append({**c, **scores})

    candidates.sort(key=lambda x: -x["risk_pct"])
    return candidates

# ── API ENDPOINTS ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "Meesho Campus Predictor API"}

@app.get("/score")
def get_scores():
    """Pull latest sheet data, score all candidates, return ranked list."""
    weights = load_weights()
    raw = fetch_sheet_data()
    candidates = build_candidates(raw, weights)
    return {
        "candidates": candidates,
        "weights": weights,
        "total": len(candidates),
        "high_risk": sum(1 for c in candidates if c["risk_pct"] >= weights["threshold"]),
        "refreshed_at": datetime.now().isoformat(),
    }

@app.get("/weights")
def get_weights():
    """Return current signal weights."""
    return load_weights()

@app.post("/weights")
async def update_weights(payload: dict):
    weights = {**DEFAULT_WEIGHTS, **payload}
    save_weights(weights)
    raw = fetch_sheet_data()
    candidates = build_candidates(raw, weights)
    return {"candidates": candidates, "weights": weights, "total": len(candidates), "high_risk": sum(1 for c in candidates if c["risk_pct"] >= weights["threshold"]), "refreshed_at": datetime.now().isoformat()}

@app.post("/outcome")
async def record_outcome(payload: dict):
    name = payload.get("name", "")
    outcome = payload.get("outcome", "")
    if outcome not in ["joined", "declined"]:
        raise HTTPException(status_code=400, detail="outcome must be joined or declined")
    save_outcome(name, outcome)
    return {"status": "recorded", "name": name, "outcome": outcome}

@app.get("/health")
def health():
    return {"status": "healthy", "sheet_id": SHEET_ID}
