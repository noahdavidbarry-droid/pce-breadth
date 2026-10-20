"""Official-source watch for Chair Warsh (feeds the email's Narrative Watch).

From federalreserve.gov (no key):
  - calendar.json: Warsh's recent and upcoming scheduled appearances
  - speeches / testimony RSS: official texts by Warsh
  - press release RSS: recent monetary-policy releases
  - FOMC calendar page: next scheduled FOMC meeting(s)
Writes data/fed_watch.json. Stdlib only.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(DATA, "fed_watch.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}
BASE = "https://www.federalreserve.gov"
WHO = "warsh"
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                       "august", "september", "october", "november", "december"], 1)}
MON_ABBR = {m[:3]: i for m, i in MONTHS.items()}


def mnum(tok):
    t = (tok or "").lower()
    return MONTHS.get(t) or (MON_ABBR.get(t) if len(t) == 3 else None)


def warn(msg):
    print(f"::warning::{msg}")
    print(msg, file=sys.stderr)


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return r.read().decode("utf-8-sig", "replace")


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def event_date(e):
    try:
        y, m = map(int, str(e.get("month", "")).split("-"))
        dd = int(re.match(r"\d+", str(e.get("days", ""))).group())
        return date(y, m, dd)
    except Exception:  # noqa: BLE001
        return None


def calendar(today):
    j = json.loads(get(f"{BASE}/json/calendar.json"))
    recent, upcoming, fomc_cal = [], [], []
    for e in j.get("events", []):
        dt = event_date(e)
        if not dt:
            continue
        blob = " ".join(str(e.get(k, "")) for k in ("title", "description", "location")).lower()
        if WHO in blob:
            item = {"date": dt.isoformat(), "time_et": e.get("time"), "type": e.get("type"),
                    "title": e.get("title"), "description": e.get("description"),
                    "location": e.get("location"), "link": (BASE + e["link"]) if str(e.get("link", "")).startswith("/") else e.get("link"),
                    "live": e.get("live")}
            if today - timedelta(days=14) <= dt < today:
                recent.append(item)
            elif today <= dt <= today + timedelta(days=45):
                upcoming.append(item)
        if e.get("type") == "FOMC" and dt >= today:
            fomc_cal.append({"date": dt.isoformat(), "title": e.get("title"), "description": e.get("description")})
    recent.sort(key=lambda x: x["date"], reverse=True)
    upcoming.sort(key=lambda x: x["date"])
    fomc_cal.sort(key=lambda x: x["date"])
    return recent, upcoming, fomc_cal[:6]


def rss(url, only_warsh=False, days=30, today=None):
    items = []
    for raw in re.findall(r"<item>(.*?)</item>", get(url), re.S):
        f = lambda tag: unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(rf"<{tag}>(.*?)</{tag}>", raw, re.S) or [None, ""])[1]).strip())  # noqa: E731
        title = f("title")
        if only_warsh and WHO not in title.lower():
            continue
        try:
            pub = parsedate_to_datetime(f("pubDate")).date()
        except Exception:  # noqa: BLE001
            pub = None
        if pub and today and pub < today - timedelta(days=days):
            continue
        items.append({"date": pub.isoformat() if pub else None, "title": title,
                      "description": f("description"), "category": f("category"), "link": f("link")})
    return items


def fomc_meetings(today):
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", get(f"{BASE}/monetarypolicy/fomccalendars.htm"))))
    meetings = []
    for m in re.finditer(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|$)", text):
        year, body = int(m.group(1)), m.group(2)
        pat = r"\b([A-Z][a-z]{2,8})(?:/([A-Z][a-z]{2,8}))? (\d{1,2})(?:-(\d{1,2}))?(\*)?"
        for mm in re.finditer(pat, body):
            if "released" in body[max(0, mm.start() - 12):mm.start()].lower():
                continue  # minutes release dates, not meetings
            m1, m2, d1, d2, star = mm.groups()
            k1, k2 = mnum(m1), (mnum(m2) if m2 else mnum(m1))
            if not k1 or not k2:
                continue
            try:
                start = date(year, k1, int(d1))
                end = date(year, k2, int(d2 or d1))
            except ValueError:
                continue
            if end < start:
                continue
            meetings.append({"start": start.isoformat(), "end": end.isoformat(),
                             "with_projections": bool(star),
                             "label": mm.group(0).rstrip("*")})
    seen, out = set(), []
    for x in sorted(meetings, key=lambda x: x["start"]):
        if x["start"] not in seen:
            seen.add(x["start"])
            out.append(x)
    upcoming = [x for x in out if x["end"] >= today.isoformat()]
    return upcoming[:3], len(out)


def main():
    today = today_et()
    out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "as_of_date_et": today.isoformat(), "source": "federalreserve.gov", "errors": {}}
    try:
        out["recent_appearances_14d"], out["upcoming_appearances_45d"], cal_fomc = calendar(today)
    except Exception as e:  # noqa: BLE001
        out["errors"]["calendar"] = f"{type(e).__name__}: {e}"
        cal_fomc = []
    for key, url, warsh_only, days in (
            ("warsh_speeches_30d", f"{BASE}/feeds/speeches.xml", True, 30),
            ("warsh_testimony_90d", f"{BASE}/feeds/testimony.xml", True, 90),
            ("press_releases_7d", f"{BASE}/feeds/press_all.xml", False, 7)):
        try:
            out[key] = rss(url, warsh_only, days, today)
        except Exception as e:  # noqa: BLE001
            out["errors"][key] = f"{type(e).__name__}: {e}"
    try:
        nxt, n_total = fomc_meetings(today)
        out["next_fomc_meetings"] = nxt
        out["fomc_meetings_parsed"] = n_total
    except Exception as e:  # noqa: BLE001
        out["errors"]["fomc_page"] = f"{type(e).__name__}: {e}"
        out["next_fomc_meetings"] = []
    if not out.get("next_fomc_meetings") and cal_fomc:
        out["next_fomc_meetings"] = [{"start": x["date"], "end": x["date"], "label": x["title"]} for x in cal_fomc[:3]]
        out["fomc_source"] = "calendar.json"
    if out.get("next_fomc_meetings"):
        out["days_to_next_fomc"] = (date.fromisoformat(out["next_fomc_meetings"][0]["start"]) - today).days
    for k, v in out["errors"].items():
        warn(f"fed_watch {k}: {v}")
    out["status"] = "ok" if not out["errors"] else ("failed" if len(out["errors"]) >= 4 else "partial")
    if out["status"] == "failed":
        print(json.dumps(out, indent=2))
        sys.exit(1)
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2)[:3000])


if __name__ == "__main__":
    main()
