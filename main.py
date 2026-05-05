"""
Meesho Campus Decline Predictor — FastAPI Backend
Reads live data from Google Sheets, scores candidates, exposes REST API.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests
import json
import os
import csv
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

app = FastAPI(title="Meesho Campus Predictor API")

BASE_DIR = Path(__file__).resolve().parent


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── CONFIG ────────────────────────────────────────────────────────────────────

SHEET_ID = os.getenv("SHEET_ID", "1SXojteQ8RpbEucxXbPRd0maLlasebibw4eVbNmNqdgI")
SHEET_NAME = os.getenv("SHEET_NAME", "Main Tracker")
WEIGHTS_FILE = Path(os.getenv("WEIGHTS_FILE", BASE_DIR / "weights.json"))
OUTCOMES_FILE = Path(os.getenv("OUTCOMES_FILE", BASE_DIR / "outcomes.csv"))

# Column name mappings from your Google Sheet headers
# Edit these if your sheet column names differ
COL_MAP = {
    "name":          "Name of the Candidate",
    "college":       "College Name",
    "role":          "Role offered",
    "cgpa":          "CGPA",
    "doj":           "DOJ",
    "offer_status":  "Offer Status",
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

# Default weights (used if weights.json doesn't exist yet). These mirror the
# frontend sliders and are the single backend source of truth for scoring.
DEFAULT_WEIGHTS = {
    "tier1": 0,
    "tier2": 0,
    "other": 0,
    "tech": 0,
    "cgpa_high": 15,
    "cgpa_mid": 8,
    "cgpa_low": 3,
    "intern6m": 12,
    "intern_tier1": 15,
    "eng_critical": 45,
    "eng_risky": 25,
    "eng_safe": -10,
    "threshold": 70,
    "medium_threshold": 45,
    "eng_jf_yes": 4,
    "eng_jf_no": -3,
    "eng_sw_yes": 1,
    "eng_sw_no": -1,
    "eng_gk_yes": 2,
    "eng_gk_no": -1,
    "eng_ga_yes": 2,
    "eng_ga_no": -0.5,
    "eng_li_mention": 3,
    "eng_li_lc": 2,
    "eng_li_c": 1.5,
    "eng_li_l": 1,
    "eng_call_pos_strong": 5,
    "eng_call_pos_mild": 3,
    "eng_call_ghost": -6,
    "eng_call_mba": -4,
    "eng_call_ppo": -4,
    "eng_call_risky": -2,
    "eng_critical_threshold": 4,
    "eng_risky_threshold": 10,
}

WEIGHT_LIMITS = {
    "tier1": (0, 30),
    "tier2": (0, 30),
    "other": (0, 30),
    "tech": (0, 30),
    "cgpa_high": (0, 30),
    "cgpa_mid": (0, 30),
    "cgpa_low": (0, 30),
    "intern6m": (0, 30),
    "intern_tier1": (0, 30),
    "eng_critical": (0, 80),
    "eng_risky": (0, 80),
    "eng_safe": (-50, 20),
    "threshold": (1, 100),
    "medium_threshold": (1, 100),
    "eng_jf_yes": (-20, 20),
    "eng_jf_no": (-20, 20),
    "eng_sw_yes": (-20, 20),
    "eng_sw_no": (-20, 20),
    "eng_gk_yes": (-20, 20),
    "eng_gk_no": (-20, 20),
    "eng_ga_yes": (-20, 20),
    "eng_ga_no": (-20, 20),
    "eng_li_mention": (-20, 20),
    "eng_li_lc": (-20, 20),
    "eng_li_c": (-20, 20),
    "eng_li_l": (-20, 20),
    "eng_call_pos_strong": (-20, 20),
    "eng_call_pos_mild": (-20, 20),
    "eng_call_ghost": (-20, 20),
    "eng_call_mba": (-20, 20),
    "eng_call_ppo": (-20, 20),
    "eng_call_risky": (-20, 20),
    "eng_critical_threshold": (-20, 30),
    "eng_risky_threshold": (-20, 30),
}

# Keep high threshold above medium threshold even if a caller supplies both.
ORDERED_THRESHOLDS = ("medium_threshold", "threshold")

# ── WEIGHTS PERSISTENCE ───────────────────────────────────────────────────────

def validate_weight_updates(payload: dict, strict: bool = True, base: Optional[dict] = None) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Weights payload must be an object.")

    updates = {}
    unknown = sorted(set(payload) - set(DEFAULT_WEIGHTS))
    if strict and unknown:
        raise HTTPException(status_code=400, detail=f"Unknown weight keys: {', '.join(unknown)}")

    for key, value in payload.items():
        if key not in DEFAULT_WEIGHTS:
            continue
        if isinstance(value, bool):
            raise HTTPException(status_code=400, detail=f"{key} must be numeric.")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{key} must be numeric.")
        low, high = WEIGHT_LIMITS[key]
        if numeric < low or numeric > high:
            raise HTTPException(status_code=400, detail=f"{key} must be between {low} and {high}.")
        updates[key] = numeric

    merged = {**DEFAULT_WEIGHTS, **(base or {}), **updates}
    if merged[ORDERED_THRESHOLDS[0]] >= merged[ORDERED_THRESHOLDS[1]]:
        raise HTTPException(status_code=400, detail="medium_threshold must be below threshold.")
    if merged["eng_critical_threshold"] >= merged["eng_risky_threshold"]:
        raise HTTPException(status_code=400, detail="eng_critical_threshold must be below eng_risky_threshold.")
    return updates


def atomic_json_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        json.dump(data, tmp, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, path)


def load_weights() -> dict:
    if WEIGHTS_FILE.exists():
        try:
            with open(WEIGHTS_FILE) as f:
                raw = json.load(f)
            updates = validate_weight_updates(raw, strict=False)
            return {**DEFAULT_WEIGHTS, **updates}
        except (OSError, json.JSONDecodeError, HTTPException):
            return DEFAULT_WEIGHTS.copy()
    return DEFAULT_WEIGHTS.copy()

def save_weights(w: dict):
    updates = validate_weight_updates(w, strict=True)
    atomic_json_write(WEIGHTS_FILE, {**DEFAULT_WEIGHTS, **updates})

# ── GOOGLE SHEETS READER ──────────────────────────────────────────────────────

def fetch_sheet_data() -> list[dict]:
    """
    Reads the Google Sheet as CSV (no API key needed — sheet must be public viewer).
    Returns a list of row dicts with raw string values.
    """
    sheet_name = quote(SHEET_NAME)
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={sheet_name}"
    try:
        resp = requests.get(url, timeout=10)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Google Sheet: {exc}") from exc
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
    except (TypeError, ValueError):
        return 7.5  # default mid-band

def parse_college_tier(college: str) -> int:
    c = re.sub(r"[^a-z0-9]+", " ", (college or "").lower()).strip()
    if not c:
        return 3

    # Explicit exceptions first, then token-aware checks so "IIIT" is not
    # accidentally treated as "IIT".
    if "iiit hyderabad" in c or "international institute of information technology hyderabad" in c:
        return 1
    if re.search(r"\biit\b", c) or "indian institute of technology" in c:
        return 1
    if re.search(r"\biisc\b", c) or "indian institute of science" in c:
        return 1
    if re.search(r"\bbits\b", c) or "birla institute of technology and science" in c:
        return 1
    if re.search(r"\b(nit|iiit|vit|srm|manipal)\b", c):
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
    """
    Maps exact sheet role values to internal codes.
    Sheet values:
      "Data Scientist - I"                        → DS-I
      "Senior Associate - Business Management track" → SA-BMT
      "Software Development Engineer - I"          → SDE-I
      "MLE-I"                                      → MLE-I
      "OR Scientist-I"                             → OR-I
    """
    r = role.strip() if role else ""
    ru = r.upper()
    # Exact / starts-with matches first (most reliable)
    if ru.startswith("OR SCIENTIST") or ru == "OR-I": return "OR-I"
    if ru.startswith("MLE") or "MACHINE LEARNING" in ru: return "MLE-I"
    if "DATA SCIENTIST" in ru or ru.startswith("DS"): return "DS-I"
    if "SOFTWARE DEVELOPMENT" in ru or ru.startswith("SDE"): return "SDE-I"
    if "SENIOR ASSOCIATE" in ru or "BUSINESS MANAGEMENT" in ru or "BMT" in ru: return "SA-BMT"
    return r or "Other"

def parse_offer_status(status: str) -> str:
    """
    Maps sheet Offer Status to declined only.
    We never mark 'joined' from the sheet until after actual joining date —
    candidates who accepted the offer are still at risk until they physically join.
    """
    if not status:
        return None
    s = status.strip().lower()
    if any(k in s for k in ["decline", "rejected", "withdrawn", "not joining", "backed out", "no show", "revoked"]):
        return "declined"
    return None  # everything else (accepted, confirmed, pending) = still active, not yet joined

# ── SCORING ENGINE ────────────────────────────────────────────────────────────

def engagement_score(c: dict, weights: dict) -> float:
    eng = 0.0
    eng += weights["eng_jf_yes"] if c["joining_form"] else weights["eng_jf_no"]
    eng += weights["eng_sw_yes"] if c["swag_form"] else weights["eng_sw_no"]
    eng += weights["eng_gk_yes"] if c["gmeet_k"] else weights["eng_gk_no"]
    eng += weights["eng_ga_yes"] if c["gmeet_a"] else weights["eng_ga_no"]
    if c["li_mention"]: eng += weights["eng_li_mention"]
    if c["li_lc"]: eng += weights["eng_li_lc"]
    if c["li_c"]: eng += weights["eng_li_c"]
    if c["li_l"]: eng += weights["eng_li_l"]
    if c["intern_months"] == 6: eng -= 2
    if c["intern_tier"] == 1: eng -= 2
    call_risk = c["call_risk"]
    if call_risk >= 5:
        eng += weights["eng_call_pos_strong"]
    elif call_risk >= 3:
        eng += weights["eng_call_pos_mild"]
    elif call_risk <= -6:
        eng += weights["eng_call_ghost"]
    elif call_risk <= -4:
        eng += weights["eng_call_mba"]
    elif call_risk <= -3:
        eng += weights["eng_call_ppo"]
    elif call_risk <= -2:
        eng += weights["eng_call_risky"]
    return round(eng, 1)

def engagement_label(score: float, weights: dict) -> str:
    if score < weights["eng_critical_threshold"]: return "critical"
    if score < weights["eng_risky_threshold"]: return "risky"
    return "safe"

def clamp_score(value: float, low: int = 5, high: int = 100) -> int:
    return min(max(int(round(value)), low), high)

def profile_market_points(c: dict, weights: dict) -> float:
    pts = 0.0
    if c["tier"] == 1:
        pts += weights["tier1"]
    elif c["tier"] == 2:
        pts += weights["tier2"]
    else:
        pts += weights["other"]

    if c["role"] in ["SDE-I", "DS-I", "MLE-I", "OR-I"]:
        pts += weights["tech"]

    if c["cgpa"] >= 8.5:
        pts += weights["cgpa_high"]
    elif c["cgpa"] >= 7.0:
        pts += weights["cgpa_mid"]
    else:
        pts += weights["cgpa_low"]

    if c["intern_months"] == 6:
        pts += weights["intern6m"]
    if c["intern_tier"] == 1:
        pts += weights["intern_tier1"]
    return round(pts, 1)

def engagement_risk_points(eng: float, weights: dict) -> float:
    """
    Convert engagement into risk without a hard cliff at the critical/risky
    boundaries. The existing sliders still control the ceiling and mid-band.
    """
    critical_threshold = weights["eng_critical_threshold"]
    risky_threshold = weights["eng_risky_threshold"]
    critical_cap = weights["eng_critical"]
    risky_anchor = weights["eng_risky"]

    if eng < critical_threshold:
        return round(min(critical_cap, risky_anchor + (critical_threshold - eng) * 4), 1)

    if eng < risky_threshold:
        span = max(risky_threshold - critical_threshold, 1)
        ratio = (risky_threshold - eng) / span
        lower_anchor = max(8, risky_anchor * 0.45)
        return round(lower_anchor + ratio * (risky_anchor - lower_anchor), 1)

    safe_discount = abs(min(weights["eng_safe"], 0))
    if safe_discount == 0:
        return 0.0
    ratio = min(max((eng - risky_threshold) / 8, 0), 1)
    return round(-safe_discount * ratio, 1)

def urgency_points(doj: str) -> int:
    if doj == "May":
        return 8
    if doj == "Jun":
        return 4
    return 0

def call_note_risk_points(call_risk: int) -> int:
    if call_risk <= -6:
        return 18
    if call_risk <= -4:
        return 14
    if call_risk <= -3:
        return 12
    if call_risk <= -2:
        return 8
    if call_risk >= 5:
        return -10
    if call_risk >= 3:
        return -6
    return 0

def follow_up_points(c: dict) -> int:
    if c["called"]:
        return 0
    if c["doj"] == "May":
        return 8
    if c["doj"] == "Jun":
        return 4
    if c["intern_months"] == 6 or c["intern_tier"] == 1:
        return 4
    return 0

def risk_category(c: dict, score: int, components: dict, label: str, weights: dict) -> tuple[str, str]:
    if c.get("outcome") == "declined":
        return "confirmed_decline", "Confirmed decline"
    if c["call_risk"] <= -6:
        return "high", "High risk"
    high_threshold = weights["threshold"]
    critical_high_threshold = max(weights["medium_threshold"], high_threshold - 5)
    supporting_watch_threshold = max(1, weights["medium_threshold"] - 10)

    if score >= high_threshold:
        return "high", "High risk"
    if label == "critical" and score >= critical_high_threshold:
        return "high", "High risk"
    if score >= weights["medium_threshold"]:
        return "watch", "Watch"
    if label == "critical":
        return "watch", "Watch"
    if score >= supporting_watch_threshold and (
        not c["joining_form"]
        or not c["gmeet_k"]
        or c["intern_months"] == 6
        or c["intern_tier"] == 1
        or c["doj"] == "May"
        or c["call_risk"] < 0
    ):
        return "watch", "Watch"
    if not c["called"] and c["doj"] == "May" and (c["cgpa"] >= 8.5 or c["tier"] == 1):
        return "watch", "Watch"
    return "low", "Low"

def risk_reasons(c: dict, eng: float, label: str, components: dict) -> list[str]:
    reasons = []
    if c.get("outcome") == "declined":
        reasons.append("Confirmed declined outcome in sheet or outcomes file.")
    if label == "critical":
        reasons.append(f"Engagement score {eng:+.1f} is critical.")
    elif label == "risky":
        reasons.append(f"Engagement score {eng:+.1f} needs watching.")
    if not c["joining_form"]:
        reasons.append("Joining dates form is not filled.")
    if not c["gmeet_k"]:
        reasons.append("Kickoff meeting was missed.")
    if c["intern_months"] == 6:
        reasons.append("6-month internship can increase PPO or alternate-offer risk.")
    if c["intern_tier"] == 1:
        reasons.append("Tier-1 internship company suggests stronger outside-option risk.")
    if c["doj"] == "May":
        reasons.append("May DOJ needs faster recruiter action.")
    elif c["doj"] == "Jun":
        reasons.append("June DOJ is approaching soon.")
    if not c["called"] and c["doj"] in {"May", "Jun"}:
        reasons.append("No recruiter call is recorded for a near-term DOJ.")
    if c["call_risk"] <= -6:
        reasons.append("Call notes indicate the candidate has been hard to reach.")
    elif c["call_risk"] <= -4:
        reasons.append("Call notes mention higher studies or similar decline risk.")
    elif c["call_risk"] <= -3:
        reasons.append("Call notes mention PPO, competing offer, or placement elsewhere.")
    elif c["call_risk"] <= -2:
        reasons.append("Call notes contain a recruiter concern.")
    elif c["call_risk"] >= 3:
        reasons.append("Positive call notes reduce the score.")
    if c["cgpa"] >= 8.5:
        reasons.append("High CGPA may correlate with stronger outside options.")
    if not reasons:
        reasons.append("No major decline signal beyond the baseline profile.")
    return reasons

def calc_risk(c: dict, weights: dict) -> dict:
    eng = engagement_score(c, weights)
    label = engagement_label(eng, weights)
    components = {
        "market": profile_market_points(c, weights),
        "engagement": engagement_risk_points(eng, weights),
        "urgency": urgency_points(c["doj"]),
        "call_notes": call_note_risk_points(c["call_risk"]),
        "follow_up": follow_up_points(c),
    }
    risk_score = clamp_score(sum(components.values()))
    components["total"] = risk_score
    category, category_label = risk_category(c, risk_score, components, label, weights)

    return {
        "risk_pct": risk_score,
        "risk_score": risk_score,
        "eng_score": eng,
        "eng_label": label,
        "category": category,
        "category_label": category_label,
        "components": components,
        "reasons": risk_reasons(c, eng, label, components),
    }

# ── OUTCOMES PERSISTENCE ──────────────────────────────────────────────────────

def load_outcome_rows() -> list[dict]:
    if not OUTCOMES_FILE.exists():
        return []
    with open(OUTCOMES_FILE, newline="") as f:
        reader = csv.DictReader(f)
        return [
            {
                "name": (row.get("name") or "").strip(),
                "outcome": (row.get("outcome") or "").strip().lower(),
                "timestamp": row.get("timestamp") or "",
            }
            for row in reader
            if (row.get("name") or "").strip()
        ]


def atomic_csv_write(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, newline="") as tmp:
        writer = csv.DictWriter(tmp, fieldnames=["name", "outcome", "timestamp"])
        writer.writeheader()
        writer.writerows(rows)
        temp_name = tmp.name
    os.replace(temp_name, path)


def load_outcomes() -> dict:
    """Return locally recorded outcomes, latest row wins for duplicate names."""
    outcomes = {}
    for row in load_outcome_rows():
        if row["outcome"] in {"joined", "declined"}:
            outcomes[row["name"]] = row["outcome"]
    return outcomes

def save_outcome(name: str, outcome: str):
    name = (name or "").strip()
    outcome = (outcome or "").strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if outcome not in {"joined", "declined"}:
        raise HTTPException(status_code=400, detail="outcome must be 'joined' or 'declined'")

    rows = [row for row in load_outcome_rows() if row["name"] != name]
    rows.append({"name": name, "outcome": outcome, "timestamp": datetime.now().isoformat()})
    atomic_csv_write(OUTCOMES_FILE, rows)

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

        # Read outcome from sheet first, then fall back to outcomes.csv
        sheet_outcome = parse_offer_status(row.get(COL_MAP["offer_status"], ""))
        csv_outcome = outcomes.get(name)
        final_outcome = sheet_outcome or csv_outcome  # sheet takes priority

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
            "outcome": final_outcome,
        }

        scores = calc_risk(c, weights)
        candidates.append({**c, **scores})

    candidates.sort(key=lambda x: -x["risk_pct"])
    return candidates

# ── API ENDPOINTS ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "Meesho Campus Predictor API"}

@app.get("/app")
def dashboard():
    index_path = BASE_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard file not found.")
    return FileResponse(index_path)

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
        "high_risk": sum(1 for c in candidates if c["category"] in {"high", "confirmed_decline"}),
        "watch": sum(1 for c in candidates if c["category"] == "watch"),
        "low": sum(1 for c in candidates if c["category"] == "low"),
        "refreshed_at": datetime.now().isoformat(),
    }

@app.get("/weights")
def get_weights():
    """Return current signal weights."""
    return load_weights()

@app.post("/weights")
async def update_weights(payload: dict):
    """Save new weights and return confirmation."""
    current = load_weights()
    updates = validate_weight_updates(payload, strict=True, base=current)
    weights = {**current, **updates}
    save_weights(weights)
    return {"status": "saved", "weights": weights}

@app.post("/weights/save")
async def save_weights_endpoint(payload: dict):
    """Save weights to disk so all users get them on next load."""
    current = load_weights()
    updates = validate_weight_updates(payload, strict=True, base=current)
    weights = {**current, **updates}
    save_weights(weights)
    return {"status": "saved", "weights": weights, "saved_at": datetime.now().isoformat()}

class OutcomePayload(BaseModel):
    name: str
    outcome: str  # "joined" or "declined"

@app.post("/outcome")
def record_outcome(payload: OutcomePayload):
    """Record a join/decline outcome for future model training."""
    outcome = payload.outcome.strip().lower()
    if outcome not in ["joined", "declined"]:
        raise HTTPException(status_code=400, detail="outcome must be 'joined' or 'declined'")
    save_outcome(payload.name, outcome)
    return {"status": "recorded", "name": payload.name.strip(), "outcome": outcome}

@app.get("/outcomes")
def get_outcomes():
    """Return locally recorded outcomes used when the sheet has no outcome."""
    outcomes = load_outcomes()
    return {"outcomes": outcomes, "total": len(outcomes)}

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "allowed_origins": ["*"],
        "sheet_configured": bool(SHEET_ID),
    }
