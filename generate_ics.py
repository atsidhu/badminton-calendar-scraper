import hashlib
import re
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

def _parse_time_range(date_obj, time_text):
    # Example: "9:00 AM - 5:15 PM (495 mins)"
    m = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)", time_text)
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

def _parse_date(date_text, fallback_date):
    # Example: "Tue, 03-Feb-26"
    m = re.search(r"\b[A-Za-z]{3},\s*\d{2}-[A-Za-z]{3}-\d{2}\b", date_text)
    if m:
        return datetime.strptime(m.group(0), "%a, %d-%b-%y").date()
    # Fallback: use the date we requested
    return fallback_date

def _extract_events(html, fallback_date):
    soup = BeautifulSoup(html, "html.parser")
    lines = [ln.strip() for ln in soup.get_text("\n").split("\n")]
    lines = [ln for ln in lines if ln]

    events = []
    i = 0
    while i < len(lines):
        name = lines[i]
        if name.lower() == ACTIVITY_NAME.lower():
            date_line = ""
            time_line = ""
            location_line = ""
            j = i + 1
            while j < len(lines) and j < i + 12:
                if lines[j].startswith("Date:"):
                    date_line = lines[j]
                elif lines[j].startswith("Time:"):
                    time_line = lines[j]
                elif lines[j].startswith("Location:"):
                    location_line = lines[j]
                if date_line and time_line and location_line:
                    break
                j += 1

            if date_line and time_line and location_line:
                date_obj = _parse_date(date_line, fallback_date)
                start_dt, end_dt = _parse_time_range(date_obj, time_line)
                if start_dt and end_dt:
                    location = location_line.replace("Location:", "").strip()
                    events.append({
                        "name": ACTIVITY_NAME,
                        "start": start_dt,
                        "end": end_dt,
                        "location": location,
                    })
        i += 1
    return events

def _uid_for(evt):
    raw = f"{evt['name']}|{evt['start'].isoformat()}|{evt['end'].isoformat()}|{evt['location']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() + "@genesis-dropin"

def generate_ics(events):
    lines = []
    lines.append("BEGIN:VCALENDAR")
    lines.append("VERSION:2.0")
    lines.append("PRODID:-//Genesis Drop-In//Badminton//EN")
    lines.append("CALSCALE:GREGORIAN")
    lines.append("X-WR-TIMEZONE:America/Edmonton")

    for evt in events:
        start_utc = evt["start"].astimezone(timezone.utc)
        end_utc = evt["end"].astimezone(timezone.utc)
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{_uid_for(evt)}")
        lines.append(f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"SUMMARY:{evt['name']}")
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
        res = requests.get(url, timeout=20)
        res.raise_for_status()
        day_events = _extract_events(res.text, day)
        all_events.extend(day_events)

    ics = generate_ics(all_events)
    import os
    os.makedirs("docs", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(ics)

if __name__ == "__main__":
    main()
