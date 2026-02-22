# scrape_events.py
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import pandas as pd
import time
import re
from typing import Tuple


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

MAX_PAGES = 3


def clean(text: str | None) -> str:
    return " ".join(text.split()) if text else "N/A"


def get_text(el) -> str:
    return clean(el.get_text()) if el else "N/A"


def parse_eventbrite_datetime(soup: BeautifulSoup, raw_html: str) -> Tuple[str, str]:
    """
    Returns (date, time) formatted as:
      date: YYYY-MM-DD
      time: HH:MM AM/PM
    """
    # Strategy 1: <time datetime="2026-03-12T18:00:00">
    time_el = soup.select_one("time[datetime]")
    if time_el:
        try:
            dt = datetime.fromisoformat(time_el.get("datetime", "").replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p")
        except ValueError:
            pass

    # Strategy 2: JSON-LD structured data in <script> tags
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
            # sometimes JSON-LD is a list
            if isinstance(data, list) and data:
                data = data[0]
            start = data.get("startDate", "") if isinstance(data, dict) else ""
            if start:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p")
        except Exception:
            continue

    # Strategy 3: regex ISO string in raw HTML
    iso = re.search(r'"startDate"\s*:\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', raw_html)
    if iso:
        try:
            dt = datetime.fromisoformat(iso.group(1))
            return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p")
        except Exception:
            pass

    # Strategy 4: human-readable patterns
    date_pat = re.compile(
        r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s*'
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+'
        r'(\d{1,2})(?:,?\s*(\d{4}))?',
        re.I
    )
    time_pat = re.compile(r'\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b', re.I)

    text = soup.get_text(" ")
    event_date = "N/A"
    event_time = "N/A"

    dm = date_pat.search(text)
    tm = time_pat.search(text)

    if dm:
        year = dm.group(3) or str(datetime.now().year)
        try:
            dt = datetime.strptime(f"{dm.group(1)} {dm.group(2)} {year}", "%B %d %Y")
            event_date = dt.strftime("%Y-%m-%d")
        except Exception:
            event_date = "N/A"

    if tm:
        event_time = tm.group(1).upper().replace(" ", "")

    return event_date, event_time


def parse_eventbrite_location(soup: BeautifulSoup) -> str:
    for sel in [
        "[data-spec='venue-name']",
        "[class*='venue-name']",
        "[class*='location-info__address']",
        "address",
    ]:
        el = soup.select_one(sel)
        if el:
            txt = clean(el.get_text())
            if txt and len(txt) < 120:
                return txt

    # fallback: something that mentions Pittsburgh
    candidates = [
        clean(el.get_text())
        for el in soup.find_all(["p", "span", "div", "address"])
        if "Pittsburgh" in clean(el.get_text())
        and 5 < len(clean(el.get_text())) < 120
    ]
    return min(candidates, key=len) if candidates else "N/A"


def clean_location(loc: str) -> str:
    if loc == "N/A" or not isinstance(loc, str):
        return loc

    # Insert space between letters and numbers e.g. "Museum1016" -> "Museum 1016"
    loc = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", loc)

    # Remove address details starting at a number block (often street number)
    loc = re.split(r"\s+\d{1,6}\s+", loc)[0].strip()

    # Strip trailing Pittsburgh/PA/zip
    loc = re.sub(r",?\s*Pittsburgh.*$", "", loc, flags=re.IGNORECASE).strip()

    # Strip some trailing street suffixes
    loc = re.sub(
        r"\s+(Road|Street|St\.?|Ave|Avenue|Blvd|Boulevard|Drive|Dr\.?|Lane|Ln\.?|Way)$",
        "",
        loc,
        flags=re.IGNORECASE
    ).strip()

    return loc if loc else "N/A"


def scrape_to_csv(output_path: str = "pittsburgh_events.csv") -> pd.DataFrame:
    """
    Scrape pgh.events and Eventbrite, clean, save to CSV, and return the dataframe.
    Safe to import (does nothing until called).
    """
    # -------------------------
    # 1) scrape pgh.events
    # -------------------------
    pgh_events: list[dict] = []

    for page_num in range(1, MAX_PAGES + 1):
        url = "https://pgh.events/" if page_num == 1 else f"https://pgh.events/?page={page_num}"
        print(f"[pgh.events] Fetching page {page_num}: {url}")

        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        day_blocks = soup.select("[class*='day-module--day']")

        if not day_blocks:
            print("  ✗ No day blocks found.")
            break

        print(f"  ✓ {len(day_blocks)} day block(s) found.")

        for day in day_blocks:
            day_time_el = day.select_one("time")
            day_date = day_time_el.get("datetime", "N/A")[:10] if day_time_el else "N/A"

            cards = day.select("[class*='event-module--event']")
            for card in cards:
                # Event name + URL
                name_el = card.select_one("[class*='event-module--mainLink']")
                event_name = get_text(name_el)

                link_el = name_el if (name_el and name_el.name == "a") else card.select_one("a[href]")
                source_url = link_el["href"] if link_el else "N/A"
                if source_url != "N/A" and source_url.startswith("/"):
                    source_url = "https://pgh.events" + source_url

                # Location (first classless <p>)
                location = "N/A"
                for p in card.select("p"):
                    if not p.get("class"):
                        txt = clean(p.get_text())
                        if txt and txt != "N/A":
                            location = txt
                            break

                # Date/time from card <time datetime="...">
                card_time_el = card.select_one("time")
                event_date = day_date
                event_time = "N/A"

                if card_time_el:
                    raw_dt = card_time_el.get("datetime", "")
                    if raw_dt and "T" in raw_dt:
                        try:
                            dt_clean = re.sub(r"[+-]\d{4}$", "", raw_dt)  # strip -0500
                            dt = datetime.strptime(dt_clean, "%Y-%m-%dT%H:%M:%S")
                            event_date = dt.strftime("%Y-%m-%d")
                            event_time = dt.strftime("%I:%M %p")
                        except ValueError:
                            event_date = raw_dt[:10]

                # Price (best-effort)
                price_el = card.select_one("[class*='price']") or card.select_one("[class*='cost']")
                price = get_text(price_el)
                if price == "N/A":
                    m = re.search(r"(Free|\$[\d,.]+)", card.get_text(), re.IGNORECASE)
                    price = m.group(0) if m else "N/A"

                pgh_events.append({
                    "event_name": event_name,
                    "date": event_date,
                    "time": event_time,
                    "location": location,
                    "price": price,
                    "source": "pgh.events",
                    "url": source_url,
                })

        print(f"  → {len(pgh_events)} events so far.")
        time.sleep(1.5)

    print(f"\n[pgh.events] Total: {len(pgh_events)} events\n")

    # -------------------------
    # 2) scrape Eventbrite list pages -> URLs
    # -------------------------
    eb_urls: list[str] = []

    for page_num in range(1, MAX_PAGES + 1):
        url = (
            "https://www.eventbrite.com/d/pa--pittsburgh/all-events/"
            if page_num == 1
            else f"https://www.eventbrite.com/d/pa--pittsburgh/all-events/?page={page_num}"
        )

        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        found: list[str] = []

        for a in soup.select("a[href*='/e/']"):
            href = a.get("href", "").split("?")[0]
            if not href:
                continue
            if href not in eb_urls and href not in found:
                found.append(href)

        eb_urls.extend(found)
        print(f"[Eventbrite] ✓ {len(found)} URLs on page {page_num}.")
        time.sleep(1.5)

    # -------------------------
    # 3) scrape Eventbrite detail pages
    # -------------------------
    eb_events: list[dict] = []

    for event_url in eb_urls:
        try:
            resp = requests.get(event_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception:
            time.sleep(1)
            continue

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

        eb_events.append({
            "event_name": event_name,
            "date": event_date,
            "time": event_time,
            "location": location,
            "price": price,
            "source": "Eventbrite",
            "url": event_url,
        })

        time.sleep(1.2)

    print(f"\n[Eventbrite] Total: {len(eb_events)} events\n")

    # -------------------------
    # 4) combine + clean + save
    # -------------------------
    all_events = pgh_events + eb_events
    df = pd.DataFrame(all_events, columns=["event_name", "date", "time", "location", "price", "source", "url"])

    df = df.fillna("N/A")

    # Basic filtering/dedup
    df = df[df["event_name"].astype(str).str.strip().str.len() > 0]
    df = df[df["event_name"] != "N/A"]
    df.drop_duplicates(subset=["event_name", "date"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Clean location + price
    df["location"] = df["location"].astype(str).apply(clean_location)
    df["price"] = df["price"].astype(str).str.rstrip(".")

    # Manual fixes (from your notebook)
    manual_fixes = {
        "Eddy TheatreWoodland": "Eddy Theatre",
        "Wyndham Grand": "Wyndham Grand Pittsburgh Downtown",
        "The Circuit Center Hot Metal": "The Circuit Center",
        "1139 Penn": "1139 Penn Ave",
        "N/A": "Pittsburgh",
    }
    df["location"] = df["location"].replace(manual_fixes)

    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print("=" * 50)
    print(f"Final Clean — {len(df)} events saved to {output_path}")
    print("=" * 50)

    return df


if __name__ == "__main__":
    # Only runs if you execute this file directly:
    #   python scrape_events.py
    scrape_to_csv("pittsburgh_events.csv")