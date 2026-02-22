from flask import Flask, render_template, request
from datetime import datetime

app = Flask(__name__)

# Step A: sample data (swap this with your scraped results later)
EVENTS = [
    {"name": "Tech Happy Hour", "price": 0.00, "location": "Pittsburgh", "date": "2026-02-27", "time": "6:00 p.m."},
    {"name": "Wine Festival", "price": 55.00, "location": "Pittsburgh", "date": "2026-03-01", "time": "2:00 p.m."},
    {"name": "Mamma Mia!", "price": 120.00, "location": "Benedum Center", "date": "2026-03-05", "time": "7:30 p.m."},
]

def to_date(date_str):
    """Convert 'YYYY-MM-DD' -> date object, or None."""
    if not date_str:
        return None
    return datetime.strptime(date_str, "%Y-%m-%d").date()

def to_float(num_str):
    """Convert string to float, or None."""
    if not num_str:
        return None
    return float(num_str)

@app.route("/")
def home():
    # Step B: read filters from the URL query string
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    max_price_str = request.args.get("max_price", "")

    start_date = to_date(start_date_str) if start_date_str else None
    end_date = to_date(end_date_str) if end_date_str else None
    max_price = to_float(max_price_str) if max_price_str else None

    # Step C: filter events
    filtered = []
    for e in EVENTS:
        e_date = to_date(e["date"])
        e_price = e["price"]

        if start_date and e_date < start_date:
            continue
        if end_date and e_date > end_date:
            continue
        if max_price is not None and e_price > max_price:
            continue

        filtered.append(e)

    # Step D: render page with results + keep form values “sticky”
    return render_template(
        "index.html",
        events=filtered,
        start_date=start_date_str,
        end_date=end_date_str,
        max_price=max_price_str
    )

if __name__ == "__main__":
    app.run(debug=True)