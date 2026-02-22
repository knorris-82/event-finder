# %%
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta
import random 
import os
import pandas as pd
import time
import re
from datetime import datetime

# %%
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
MAX_PAGES = 3
def clean(text):
    return " ".join(text.split()) if text else "N/A"
def get_text(el):
    return clean(el.get_text()) if el else "N/A"

# %%
#Scrape pgh.events

pgh_events = []

for page_num in range(1, MAX_PAGES + 1):
    url = ("https://pgh.events/" if page_num == 1
           else f"https://pgh.events/?page={page_num}")
    print(f"[pgh.events] Fetching page {page_num}: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"  ✗ Request failed: {e}")
        break

    soup = BeautifulSoup(response.text, "html.parser")
    day_blocks = soup.select("[class*='day-module--day']")

    if not day_blocks:
        print("  ✗ No day blocks found.")
        break

    print(f"  ✓ {len(day_blocks)} day block(s) found.")

    for day in day_blocks:

        # Date comes from the day-level <time datetime="2026-02-22">
        day_time_el = day.select_one("time")
        day_date = day_time_el.get("datetime", "N/A")[:10] if day_time_el else "N/A"

        cards = day.select("[class*='event-module--event']")
        for card in cards:

            # Event Name
            name_el    = card.select_one("[class*='event-module--mainLink']")
            event_name = get_text(name_el)

            # URL
            link_el    = name_el if (name_el and name_el.name == "a") else card.select_one("a[href]")
            source_url = link_el["href"] if link_el else "N/A"
            if source_url != "N/A" and source_url.startswith("/"):
                source_url = "https://pgh.events" + source_url

            # Location: first classless <p> in the card 
            # Confirmed: <p class=''>Thunderbird Cafe & Music Hall</p>
            location = "N/A"
            for p in card.select("p"):
                if not p.get("class"):          # only classless <p> tags
                    txt = clean(p.get_text())
                    if txt and txt != "N/A":
                        location = txt
                        break                   # stop at the first one (venue)

            # Date & Time from the card-level <time> tag
            # Confirmed: datetime='2026-02-22T07:00:00-0500'
            card_time_el = card.select_one("time")
            event_date   = day_date   # fallback to day date
            event_time   = "N/A"

            if card_time_el:
                raw_dt = card_time_el.get("datetime", "")
                if raw_dt and "T" in raw_dt:
                    try:
                        # Strip timezone offset for parsing
                        dt_clean = re.sub(r'[+-]\d{4}$', '', raw_dt)
                        dt = datetime.strptime(dt_clean, "%Y-%m-%dT%H:%M:%S")
                        event_date = dt.strftime("%Y-%m-%d")
                        event_time = dt.strftime("%I:%M %p")
                    except ValueError:
                        event_date = raw_dt[:10]

            # Price
            price_el = card.select_one("[class*='price']") or card.select_one("[class*='cost']")
            price    = get_text(price_el)
            if price == "N/A":
                m = re.search(r'(Free|\$[\d,.]+)', card.get_text(), re.IGNORECASE)
                price = m.group(0) if m else "N/A"

            pgh_events.append({
                "event_name": event_name,
                "date":       event_date,
                "time":       event_time,
                "location":   location,
                "price":      price,
                "source":     "pgh.events",
                "url":        source_url,
            })

    print(f"  → {len(pgh_events)} events so far.")
    time.sleep(1.5)

print(f"\n[pgh.events] Total: {len(pgh_events)} events\n")
    

# %%
#scrape eventbrite

def parse_eventbrite_datetime(soup, raw_html):
    # Strategy 1: <time datetime="2026-03-12T18:00:00">
    time_el = soup.select_one("time[datetime]")
    if time_el:
        try:
            dt = datetime.fromisoformat(time_el.get("datetime","").replace("Z","+00:00"))
            return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p")
        except ValueError: pass
    # Strategy 2: JSON-LD structured data in <script> tags
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
            start = data.get("startDate", "")
            if start:
                dt = datetime.fromisoformat(start.replace("Z","+00:00"))
                return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p")
        except: continue
    # Strategy 3: regex ISO string in raw HTML
    iso = re.search(r'"startDate"\s*:\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', raw_html)
    if iso:
        try:
            dt = datetime.fromisoformat(iso.group(1))
            return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p")
        except: pass
    # Strategy 4: human-readable text patterns
    date_pat = re.compile(r'(?:Mon|Tue|...|Sun),?\s*(January|...|December)\s+(\d{1,2})(?:,?\s*(\d{4}))?', re.I)
    time_pat = re.compile(r'\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b', re.I)
    text = soup.get_text(" ")
    event_date = event_time = "N/A"
    dm = date_pat.search(text)
    tm = time_pat.search(text)
    if dm:
        try:
            dt = datetime.strptime(f"{dm.group(1)} {dm.group(2)} {dm.group(3) or 2026}", "%B %d %Y")
            event_date = dt.strftime("%Y-%m-%d")
        except: event_date = f"{dm.group(1)} {dm.group(2)}, 2026"
    if tm: event_time = tm.group(1).upper().replace(" ","")
    return event_date, event_time

def parse_eventbrite_location(soup):
    for sel in ["[data-spec='venue-name']", "[class*='venue-name']",
                   "[class*='location-info__address']", "address"]:
        el = soup.select_one(sel)
        if el:
            txt = clean(el.get_text())
            if txt and len(txt) < 100: return txt
    candidates = [clean(el.get_text()) for el in soup.find_all(["p","span","div","address"])
                  if "Pittsburgh" in clean(el.get_text()) and 5 < len(clean(el.get_text())) < 80]
    return min(candidates, key=len) if candidates else "N/A"

# %%
eb_urls = []
for page_num in range(1, MAX_PAGES + 1):
    url = ("https://www.eventbrite.com/d/pa--pittsburgh/all-events/"
           if page_num == 1 else f".../?page={page_num}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e: break
    soup = BeautifulSoup(response.text, "html.parser")
    found = []
    for a in soup.select("a[href*='/e/']"):
        href = a["href"].split("?")[0]
        if href not in eb_urls and href not in found: found.append(href)
    eb_urls.extend(found); print(f"  ✓ {len(found)} URLs on page {page_num}.")
    time.sleep(1.5)

# %%
eb_events = []
for i, event_url in enumerate(eb_urls):
    try:
        resp = requests.get(event_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except: time.sleep(1); continue
    detail = BeautifulSoup(resp.text, "html.parser")
    name_el = detail.select_one("h1") or detail.select_one("[class*='event-title']")
    event_name = get_text(name_el)
    event_date, event_time = parse_eventbrite_datetime(detail, resp.text)
    location = parse_eventbrite_location(detail)
    price_el = detail.select_one("[class*='ticket-price']") or detail.select_one("[class*='conversion-bar']")
    price = get_text(price_el)
    if price == "N/A":
        m = re.search(r"(Free|\$[\d,.]+)", resp.text)
        price = m.group(0).capitalize() if m else "N/A"
    eb_events.append({"event_name":event_name,"date":event_date,"time":event_time,
        "location":location,"price":price,"source":"Eventbrite","url":event_url})
    time.sleep(1.2)
print(f"\n[Eventbrite] Total: {len(eb_events)} events\n")

# %%
#combine and save

all_events = pgh_events + eb_events

if not all_events:
    print("No events collected.")
else:
    df = pd.DataFrame(all_events, columns=[
        "event_name", "date", "time", "location", "price", "source", "url"
    ])
    df = df[df["event_name"].str.strip().str.len() > 0]
    df = df[df["event_name"] != "N/A"]
    df.drop_duplicates(subset=["event_name", "date"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    df.to_csv("pittsburgh_events.csv", index=False, encoding="utf-8-sig")

    print("=" * 50)
    print(f" {len(df)} events saved to pittsburgh_events.csv")
    print("=" * 50)
    print(df.to_string(index=False))

# %%
# Fix 1: NaN → "N/A" for all fields
df = df.fillna("N/A")

# Fix 2: improved location cleaner 
def clean_location(loc):
    if loc == "N/A" or not isinstance(loc, str):
        return loc
    # Split where a number follows letters with no space e.g. "Theatre5" or "Museum1016"
    loc = re.sub(r'([a-zA-Z])(\d)', r'\1', loc)
    # Also remove anything from a standalone number onwards e.g. "  1016 North..."
    loc = re.split(r'\s+\d{1,5}\s+', loc)[0].strip()
    # Strip trailing Pittsburgh/PA/zip
    loc = re.sub(r',?\s*Pittsburgh.*$', '', loc, flags=re.IGNORECASE).strip()
    # Strip trailing street suffixes left over
    loc = re.sub(r'\s+(Road|Street|Ave|Avenue|Blvd|Boulevard|Drive|Lane|Way)$', '', loc, flags=re.IGNORECASE).strip()
    return loc if loc else "N/A"

df["location"] = df["location"].apply(clean_location)

# Fix 3: clean up price formatting ("$450.00." → "$450.00")
df["price"] = df["price"].str.rstrip(".")

df.to_csv("pittsburgh_events.csv", index=False, encoding="utf-8-sig")
print(f"Clean 2 — {len(df)} events")
print(df[["event_name", "date", "time", "location", "price"]].to_string(index=False))

# %%
#clean

df = pd.read_csv("pittsburgh_events.csv")
df = df.fillna("N/A")

# Manual overrides for the remaining problem locations
manual_fixes = {
    "Eddy TheatreWoodland":         "Eddy Theatre",
    "Wyndham Grand":                "Wyndham Grand Pittsburgh Downtown",
    "The Circuit Center Hot Metal": "The Circuit Center",
    "1139 Penn":                    "1139 Penn Ave",
    "N/A":                          "Pittsburgh",  # St. Practice Day only has "Pittsburgh" — leave as city
}

df["location"] = df["location"].replace(manual_fixes)

# Fix price trailing dot just in case
df["price"] = df["price"].str.rstrip(".")

df.to_csv("pittsburgh_events.csv", index=False, encoding="utf-8-sig")
print(f"Final Clean— {len(df)} ")
print(df[["event_name", "date", "time", "location", "price"]].to_string(index=False))




