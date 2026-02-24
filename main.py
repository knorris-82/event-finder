from __future__ import annotations

from flask import Flask, render_template, request, redirect, session, url_for
import pandas as pd

from config import LATEST_OPTIONS_FILE, RECOMMENDATION_SAMPLE_FILE
from recommend import UserPreferences, build_event_suggestions, score_candidates
from utils import ensure_project_directories

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"  # for local dev; change for production

REQUIRED_INPUT_COLUMNS = [
    "event_name",
    "date",
    "time",
    "location",
    "price",
    "source",
    "url",
]


def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    missing_columns = [c for c in REQUIRED_INPUT_COLUMNS if c not in df.columns]
    if missing_columns:
        raise ValueError("Dataset is missing required columns: " + ", ".join(missing_columns))

    normalized = df[REQUIRED_INPUT_COLUMNS].copy()
    normalized["name"] = normalized["event_name"].fillna("").astype(str).str.strip()

    for column in ["source", "location", "price", "url", "date", "time"]:
        normalized[column] = normalized[column].fillna("").astype(str).str.strip()

    normalized = normalized[normalized["name"] != ""]
    normalized = normalized.drop(columns=["event_name"])

    normalized = normalized.drop_duplicates(
        subset=["source", "name", "date", "time", "location"]
    ).reset_index(drop=True)
    return normalized


def _load_dataset() -> pd.DataFrame:
    ensure_project_directories()

    if not RECOMMENDATION_SAMPLE_FILE.exists():
        raise FileNotFoundError(
            f"Latest processed dataset not found: {RECOMMENDATION_SAMPLE_FILE}. "
            "Run data collection first to generate latest event data."
        )

    df = pd.read_csv(RECOMMENDATION_SAMPLE_FILE)
    df = _ensure_schema(df)

    # keep same behavior as CLI (write latest options file)
    if LATEST_OPTIONS_FILE != RECOMMENDATION_SAMPLE_FILE:
        LATEST_OPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(LATEST_OPTIONS_FILE, index=False)

    return df


# Load once at startup (simple local approach)
try:
    EVENTS_DF = _load_dataset()
except Exception as exc:
    EVENTS_DF = None
    LOAD_ERROR = str(exc)
else:
    LOAD_ERROR = None


@app.route("/")
def menu():
    message = None
    if LOAD_ERROR:
        message = f"Dataset load error: {LOAD_ERROR}"
    return render_template("menu.html", message=message)


# ---- Wizard steps (CLI questions, in order) ----

@app.route("/wizard/budget", methods=["GET", "POST"])
def wizard_budget():
    if request.method == "POST":
        raw = (request.form.get("value") or "").strip()
        try:
            budget = float(raw) if raw else 75.0
        except ValueError:
            return render_template("step.html",
                                   title="Max event budget (USD)",
                                   help_text="Enter a number (default 75.0).",
                                   input_type="number", step="0.01", min="0",
                                   default="75.0", error="Invalid number.")
        session["budget"] = max(0.0, budget)
        return redirect(url_for("wizard_date"))

    return render_template("step.html",
                           title="Max event budget (USD)",
                           help_text="Default is 75.0",
                           input_type="number", step="0.01", min="0",
                           default=str(session.get("budget", 75.0)),
                           placeholder="75.00")


@app.route("/wizard/date", methods=["GET", "POST"])
def wizard_date():
    if request.method == "POST":
        raw = (request.form.get("value") or "").strip()
        # Blank means "any date"
        if not raw:
            session["event_date"] = ""
            return redirect(url_for("wizard_period"))

        parsed = pd.to_datetime(raw, errors="coerce")
        if pd.isna(parsed):
            return render_template("step.html",
                                   title="Event date (optional)",
                                   help_text="Pick a date or leave blank for any date.",
                                   input_type="date",
                                   default=session.get("event_date", ""),
                                   error="Invalid date; try again or leave blank.")
        session["event_date"] = pd.Timestamp(parsed).strftime("%Y-%m-%d")
        return redirect(url_for("wizard_period"))

    return render_template("step.html",
                           title="Event date (optional)",
                           help_text="Leave blank for any date.",
                           input_type="date",
                           default=session.get("event_date", ""))


@app.route("/wizard/period", methods=["GET", "POST"])
def wizard_period():
    if request.method == "POST":
        period = (request.form.get("value") or "any").strip().lower()
        if period not in {"morning", "afternoon", "evening", "any"}:
            period = "any"
        session["preferred_period"] = period
        return redirect(url_for("wizard_max_results"))

    options = [
        {"value": "any", "label": "Any"},
        {"value": "morning", "label": "Morning"},
        {"value": "afternoon", "label": "Afternoon"},
        {"value": "evening", "label": "Evening"},
    ]

    return render_template("step.html",
                           title="Preferred time of day",
                           help_text="Choose morning, afternoon, evening, or any.",
                           input_type="select",
                           options=options,
                           default=session.get("preferred_period", "any"))


@app.route("/wizard/max-results", methods=["GET", "POST"])
def wizard_max_results():
    if request.method == "POST":
        raw = (request.form.get("value") or "").strip()
        try:
            max_results = int(raw) if raw else 3
        except ValueError:
            return render_template("step.html",
                                   title="Number of suggestions to generate",
                                   help_text="Enter an integer (default 3).",
                                   input_type="number", min="1", step="1",
                                   default=str(session.get("max_results", 3)),
                                   error="Invalid integer.")
        session["max_results"] = max(1, max_results)
        return redirect(url_for("wizard_generate"))

    return render_template("step.html",
                           title="Number of suggestions to generate",
                           help_text="Default is 3",
                           input_type="number", min="1", step="1",
                           default=str(session.get("max_results", 3)))


@app.route("/wizard/generate")
def wizard_generate():
    if EVENTS_DF is None:
        return redirect(url_for("menu"))

    prefs = UserPreferences(
        budget=float(session.get("budget", 75.0)),
        preferred_period=str(session.get("preferred_period", "any")),
        max_results=int(session.get("max_results", 3)),
        event_date=(session.get("event_date") or None) or None,
    )

    scored = score_candidates(EVENTS_DF, prefs)
    plans = build_event_suggestions(scored, prefs)

    session["generated_plans"] = plans  # stored in session cookie (ok for small payload)

    if not plans:
        # mimic CLI message, then send them back to menu
        session["message"] = (
            "No suggestions matched current constraints. "
            "Try a different date, period, or higher budget."
        )
        return redirect(url_for("menu"))

    return redirect(url_for("suggestions"))


@app.route("/suggestions")
def suggestions():
    plans = session.get("generated_plans", [])
    return render_template("suggestions.html", plans=plans)


@app.route("/exit")
def exit_app():
    session.clear()
    return render_template("menu.html", message="Session cleared. (This is the web version of Exit.)")


if __name__ == "__main__":
    app.run(debug=True)