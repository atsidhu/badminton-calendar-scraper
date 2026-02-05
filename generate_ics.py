import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE_URL = (
    "https://bookings.genesis-centre.ca/genesis/public/Category/ClassList"
    "?CategoryGUID=6271ed90-ea22-4cd0-a068-73501e2de951"
    "&Participant=00000000-0000-0000-0000-000000000000"
    "&StartDate={date}"
)

TZ = ZoneInfo("America/Edmonton")
DAYS_AHEAD = 7
ACTIVITY_NAME = "Badminton"
OUTPUT_PATH = "docs/calendar.ics"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
}

_TIME_RE = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)")


def _parse_time_range(date_obj, text):
    m = _TIME_RE.search(text)
    if not m:
        return None, None
    start_str, end_str = m.group(1), m.group(2)
    start_dt = datetime.strptime(start_str, "%I:%M %p").replace(
        year=date_obj.year, month=date_obj.month, day=date_obj.day, tzinfo=TZ
    )
    end_dt = datetime.strptime(end_str, "%I:%M %p").replace(
        year=date_obj.year, month=date_obj.month, day=date_obj.day, tzinfo=TZ
    )
    return start_dt, end_dt


def _extract_events_from_table(html, fallback_date):
    """
    Parse the structured table on the page:
      Date | Time | Drop-in (Activity) | Location | Availability
    and only keep rows where activity == Badminton.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find a table that looks like the drop-in list.
    tables = soup.find_all("table")
    if not tables:
        return []

    events = []
    year = fallback_date.year

    for table in tables:
        # Heuristic: the correct table contains headers including "Drop-in" and "Location"
        header_text = " ".join(th.get_text(" ", strip=True) for th in table.find_all("th"))
        if "Drop-in" not in header_text or "Location" not in header_text:
            continue

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue

            date_txt = tds[0].get_text(" ", strip=True)  # e.g., "Tue 10 Feb"
            time_txt = tds[1].get_text(" ", strip=True)  # e.g., "9:00 AM - 5:15 PM 495 mins"
            activity_txt = tds[2].get_text(" ", strip=True)  # e.g., "Badminton"
            location_txt = tds[3].get_text(" ", strip=True)

            if activity_txt.strip().lower() != ACTIVITY_NAME.lower():
                continue

            # Parse date like "Tue 10 Feb" (year from fallback_date)
            m = re.search(r"\b([A-Za-z]{3})\s+(\d{1,2})\s+([A-Za-z]{3})\b", date_txt)
            if not m:
                continue
            day_num = int(m.group(2))
            mon_abbr = m.group(3)

            try:
                date_obj = datetime.strptime(f"{day_num} {mon_abbr} {year}", "%d %b %Y").date()
            except ValueError:
                continue

            start_dt, end_dt = _parse_time_range(date_obj, time_txt)
            if not start_dt or not end_dt:
                continue

            events.append(
                {
                    "name": ACTIVITY_NAME,
                    "start": start_dt,
                    "end": end_dt,
                    "location": location_txt,
                }
            )

        # If we found events in this table, stop (avoid picking up other unrelated tables)
        if events:
            return events

    return []


def merge_events_ignore_location(events, merge_gap_minutes=0):
    """
    Merge overlapping (or adjacent) events per day, ignoring location.
    """
    if not events:
        return []

    gap = timedelta(minutes=merge_gap_minutes)

    by_day = defaultdict(list)
    for e in events:
        by_day[e["start"].date()].append(e)

    merged = []
    for _, day_events in by_day.items():
        day_events.sort(key=lambda e: e["start"])

        cur_start = day_events[0]["start"]
        cur_end = day_events[0]["end"]

        for e in day_events[1:]:
            s, t = e["start"], e["end"]
            if s <= cur_end + gap:
                cur_end = max(cur_end, t)
            else:
                merged.append(
                    {
                        "name": ACTIVITY_NAME,
                        "start": cur_start,
                        "end": cur_end,
                        "location": "Genesis Centre",
                    }
                )
                cur_start, cur_end = s, t

        merged.append(
            {
                "name": ACTIVITY_NAME,
                "start": cur_start,
                "end": cur_end,
                "location": "Genesis Centre",
            }
        )

    merged.sort(key=lambda e: e["start"])
    return merged


def _uid_for(evt):
    # stable UID for the merged window
    raw = f"{evt['name']}|{evt['start'].isoformat()}|{evt['end'].isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() + "@genesis-dropin"


def generate_ics(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Genesis Drop-In//Badminton//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-TIMEZONE:America/Edmonton",
    ]

    for evt in events:
        start_utc = evt["start"].astimezone(timezone.utc)
        end_utc = evt["end"].astimezone(timezone.utc)

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{_uid_for(evt)}")
        lines.append(f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"SUMMARY:{evt['name']} (Drop-in window)")
        lines.append(f"LOCATION:{evt['location']}")
        lines.append("DESCRIPTION:Genesis Centre Drop-In Calendar (Badminton)")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    today = datetime.now(TZ).date()
    all_events = []

    for offset in range(DAYS_AHEAD):
        day = today + timedelta(days=offset)
        url = BASE_URL.format(date=day.isoformat())
        res = requests.get(url, headers=HEADERS, timeout=20)
        res.raise_for_status()

        # Robust: parse only the structured drop-in table and filter activity == Badminton
        day_events = _extract_events_from_table(res.text, day)
        all_events.extend(day_events)

    # Merge overlapping windows per day (ignore court/location)
    all_events = merge_events_ignore_location(all_events, merge_gap_minutes=0)

    ics = generate_ics(all_events)

    import os

    os.makedirs("docs", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(ics)


if __name__ == "__main__":
    main()

