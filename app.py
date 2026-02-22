from flask import Flask, render_template, request, redirect, url_for
import os
import pandas as pd
from datetime import datetime, timedelta

# Optional: only if you want manual refresh
from scrape_events import scrape_to_csv

app = Flask(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CSV_PATH = os.path.join(DATA_DIR, "pittsburgh_events.csv")

def load_events_df() -> pd.DataFrame:
    # Always start with a consistent dataframe
    cols = ["event_name", "date", "time", "location", "price", "source", "url"]

    if not os.path.exists(CSV_PATH):
        df = pd.DataFrame(columns=cols)
    else:
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
        # Ensure any missing columns exist (just in case)
        for c in cols:
            if c not in df.columns:
                df[c] = ""

    # ALWAYS add date_dt (even if df is empty)
    df["date_dt"] = pd.to_datetime(df["date"], errors="coerce")

    return df

def parse_price_to_number(price_str: str) -> float | None:
    """
    Converts '$12.50' -> 12.50, 'Free' -> 0.0, 'N/A' -> None
    """
    if not price_str:
        return None
    s = price_str.strip()
    if s.lower() == "free":
        return 0.0
    if s.startswith("$"):
        try:
            return float(s.replace("$", "").replace(",", ""))
        except ValueError:
            return None
    return None

@app.route("/", methods=["GET"])
def index():
    df = load_events_df()

    # Your user inputs (example)
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    max_price = request.args.get("max_price", "").strip()

    # Limit the selectable/searchable range to 2 weeks out (per your project direction)
    today = datetime.now().date()
    two_weeks_out = today + timedelta(days=14)

    # Filter by date range if provided
    if start_date:
        start_dt = pd.to_datetime(start_date, errors="coerce")
        if pd.notna(start_dt):
            df = df[df["date_dt"] >= start_dt]

    if end_date:
        end_dt = pd.to_datetime(end_date, errors="coerce")
        if pd.notna(end_dt):
            df = df[df["date_dt"] <= end_dt]

    # Enforce "no more than 2 weeks out" regardless of user input
    df = df[df["date_dt"].dt.date <= two_weeks_out]

    # Filter by max price if provided
    if max_price:
        try:
            max_price_val = float(max_price)
            df["price_num"] = df["price"].apply(parse_price_to_number)
            df = df[df["price_num"].notna() & (df["price_num"] <= max_price_val)]
        except ValueError:
            pass

    # Sort
    df = df.sort_values(["date_dt", "time", "event_name"], na_position="last")

    # Convert to records for template
    events = df.drop(columns=["date_dt"], errors="ignore").to_dict(orient="records")

    return render_template(
        "index.html",
        events=events,
        today=today.isoformat(),
        two_weeks_out=two_weeks_out.isoformat(),
        start_date=start_date,
        end_date=end_date,
        max_price=max_price
    )

@app.route("/refresh", methods=["POST"])
def refresh():
    os.makedirs(DATA_DIR, exist_ok=True)
    scrape_to_csv(CSV_PATH)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)