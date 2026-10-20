"""Daily grain + energy futures snapshot and 3:2:1 crack spread.

Writes data/commodities.json (read by the Warsh indicators email task) and
appends one row per trading day to data/commodities_history.csv.

Sources (front-month futures), tried in order, each in a single batch request:
  1. CNBC's public quote feed (official settlement price once posted)
  2. Yahoo Finance quote API, using a browser-style cookie + crumb
If both fail for an instrument, its last good value from the previous
commodities.json is kept and marked stale, so the email never goes blank.
Day-over-day change uses our own history file first (the prior trading day's
recorded price), falling back to the source's previous close.
Standard library only. Source errors are emitted as GitHub Actions warnings.
"""
import csv
import http.cookiejar
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(DATA, "commodities.json")
HIST = os.path.join(DATA, "commodities_history.csv")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "application/json,text/plain,*/*"}
ET = timezone(timedelta(hours=-4))  # only used to date-stamp; settle dates come from the source

# key: (label, cnbc symbol, yahoo symbol, unit, plausible range in output unit)
INSTRUMENTS = {
    "corn":        ("Corn",          "@C.1",  "ZC=F", "$/bu",  (1.5, 15)),
    "soybeans":    ("Soybeans",      "@S.1",  "ZS=F", "$/bu",  (4, 30)),
    "wheat":       ("Wheat",         "@W.1",  "ZW=F", "$/bu",  (2, 25)),
    "wti":         ("WTI Crude",     "@CL.1", "CL=F", "$/bbl", (10, 250)),
    "rbob":        ("RBOB Gasoline", "@RB.1", "RB=F", "$/gal", (0.5, 8)),
    "heating_oil": ("Heating Oil",   "@HO.1", "HO=F", "$/gal", (0.5, 10)),
}
GRAINS = {"corn", "soybeans", "wheat"}

_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def warn(msg):
    print(f"::warning::{msg}")
    print(msg, file=sys.stderr)


def get(url, timeout=20):
    with _opener.open(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def num(v):
    if v in (None, "", "UNCH", "N/A"):
        return None
    try:
        return float(str(v).replace(",", "").replace("+", "").replace("%", ""))
    except ValueError:
        return None


def normalize(key, v):
    """Grain futures are quoted in cents/bu; convert to $/bu."""
    v = num(v)
    if v is not None and key in GRAINS and v > 50:
        v /= 100.0
    return v


def plausible(key, v):
    lo, hi = INSTRUMENTS[key][4]
    return v is not None and lo <= v <= hi


def from_cnbc():
    out = {}
    syms = urllib.parse.quote("|".join(i[1] for i in INSTRUMENTS.values()))
    url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
           f"?symbols={syms}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
    try:
        rows = json.loads(get(url))["FormattedQuoteResult"]["FormattedQuote"]
    except Exception as e:  # noqa: BLE001
        warn(f"cnbc: {type(e).__name__}: {e}")
        return out
    by_sym = {r.get("symbol"): r for r in rows}
    for key, (_, sym, _, _, _) in INSTRUMENTS.items():
        r = by_sym.get(sym)
        if not r or r.get("code") not in (0, "0", None):
            warn(f"cnbc {sym}: missing or error in response")
            continue
        settle, sdate = r.get("settlePrice"), r.get("settleDate")
        last_date = (r.get("last_time") or "")[:10] or None
        # Prefer the official settlement once it is posted for the latest session.
        if num(settle) is not None and sdate and (last_date is None or sdate >= last_date):
            price, as_of, kind = normalize(key, settle), sdate, "settle"
        else:
            price, as_of, kind = normalize(key, r.get("last")), last_date, "last"
        prev = normalize(key, r.get("previous_day_closing"))
        if kind == "settle" and prev == price:
            prev = None  # feed already rolled: previous close == today's settle
        out[key] = {"price": price, "prev_close": prev, "as_of": as_of, "price_type": kind,
                    "contract": r.get("name"), "source": "cnbc"}
    return out


def from_yahoo():
    out = {}
    try:
        try:
            get("https://fc.yahoo.com")  # sets the session cookie; returns 404, that's fine
        except Exception:  # noqa: BLE001
            pass
        crumb = get("https://query1.finance.yahoo.com/v1/test/getcrumb").strip()
        if not crumb or len(crumb) > 40 or "<" in crumb:
            raise ValueError(f"bad crumb {crumb[:40]!r}")
        syms = ",".join(i[2] for i in INSTRUMENTS.values())
        rows = json.loads(get("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
                              f"{urllib.parse.quote(syms)}&crumb={urllib.parse.quote(crumb)}"))
        rows = rows["quoteResponse"]["result"]
    except Exception as e:  # noqa: BLE001
        warn(f"yahoo: {type(e).__name__}: {e}")
        return out
    by_sym = {r.get("symbol"): r for r in rows}
    for key, (_, _, sym, _, _) in INSTRUMENTS.items():
        r = by_sym.get(sym)
        if not r:
            warn(f"yahoo {sym}: missing from response")
            continue
        ts = r.get("regularMarketTime")
        as_of = datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d") if ts else None
        out[key] = {"price": normalize(key, r.get("regularMarketPrice")),
                    "prev_close": normalize(key, r.get("regularMarketPreviousClose")),
                    "as_of": as_of, "price_type": "last", "contract": r.get("shortName"),
                    "source": "yahoo"}
    return out


def history_prev(key, as_of):
    """Most recent recorded price for key on a date before as_of."""
    if not os.path.exists(HIST) or not as_of:
        return None
    best = None
    with open(HIST) as f:
        for r in csv.DictReader(f):
            if r["date"] < as_of and num(r.get(key)) is not None:
                best = num(r[key])
    return best


def fetch_all(previous):
    cnbc = from_cnbc()
    need_yahoo = any(not (cnbc.get(k) and plausible(k, cnbc[k]["price"])) for k in INSTRUMENTS)
    yahoo = from_yahoo() if need_yahoo else {}
    result = {}
    for key in INSTRUMENTS:
        q = None
        for cand in (cnbc.get(key), yahoo.get(key)):
            if cand and plausible(key, cand["price"]):
                q = dict(cand)
                break
            if cand:
                warn(f"{cand['source']} {key}: implausible price {cand['price']}")
        if q:
            hp = history_prev(key, q["as_of"])
            if hp is not None:
                q["prev_close"], q["prev_source"] = hp, "history"
            elif plausible(key, q.get("prev_close")):
                q["prev_source"] = q["source"]
            else:
                q["prev_close"] = None
            q["stale"] = False
            result[key] = q
        elif previous.get(key) and previous[key].get("price") is not None:
            p = dict(previous[key])
            p["stale"] = True
            result[key] = p
    return result


def crack(r, h, w):
    """3:2:1 crack spread, $/bbl."""
    if None in (r, h, w):
        return None
    return (2 * r * 42 + 1 * h * 42 - 3 * w) / 3


def main():
    previous = {}
    if os.path.exists(OUT):
        try:
            with open(OUT) as f:
                previous = json.load(f).get("instruments", {})
        except Exception:  # noqa: BLE001
            previous = {}

    fetched = fetch_all(previous)
    inst = {}
    for key, (label, _, _, unit, _) in INSTRUMENTS.items():
        q = fetched.get(key)
        if not q:
            inst[key] = {"label": label, "unit": unit, "price": None, "stale": True, "source": None}
            continue
        chg = None
        if q.get("prev_close") and not q["stale"]:
            chg = round((q["price"] / q["prev_close"] - 1) * 100, 2)
        elif q["stale"]:
            chg = q.get("change_pct")
        inst[key] = {"label": label, "unit": unit,
                     "price": round(q["price"], 4),
                     "prev_close": round(q["prev_close"], 4) if q.get("prev_close") else None,
                     "change_pct": chg, "as_of": q.get("as_of"),
                     "price_type": q.get("price_type"), "contract": q.get("contract"),
                     "source": q.get("source"), "prev_source": q.get("prev_source"),
                     "stale": q["stale"]}

    g = lambda k, f: inst.get(k, {}).get(f)  # noqa: E731
    cs = crack(g("rbob", "price"), g("heating_oil", "price"), g("wti", "price"))
    cs_prev = crack(g("rbob", "prev_close"), g("heating_oil", "prev_close"), g("wti", "prev_close"))
    spread = {"value": round(cs, 2) if cs is not None else None,
              "prev": round(cs_prev, 2) if cs_prev is not None else None,
              "change": round(cs - cs_prev, 2) if None not in (cs, cs_prev) else None,
              "unit": "$/bbl",
              "formula": "(2*RBOB*42 + 1*HO*42 - 3*WTI) / 3"}
    if spread["change"] is not None:
        spread["direction"] = "widening" if spread["change"] > 0 else ("narrowing" if spread["change"] < 0 else "flat")
        spread["change_pct"] = round(spread["change"] / abs(cs_prev) * 100, 2) if cs_prev else None
    spread["stale"] = any(g(k, "stale") for k in ("rbob", "heating_oil", "wti"))

    missing = [k for k, v in inst.items() if v.get("price") is None]
    stale = [k for k, v in inst.items() if v.get("stale") and v.get("price") is not None]
    if len(missing) == len(inst):
        status = "failed"
    elif len(missing) + len(stale) == len(inst):
        status = "stale"          # nothing fresh today; last good values kept
    elif missing or stale:
        status = "partial"
    else:
        status = "ok"

    out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "status": status, "stale_instruments": stale, "missing_instruments": missing,
           "instruments": inst, "crack_321": spread}
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)

    # history: one row per as_of date (WTI's date), only with fresh data
    day = g("wti", "as_of") or out["generated_utc"][:10]
    if status in ("ok", "partial"):
        rows = []
        if os.path.exists(HIST):
            with open(HIST) as f:
                rows = [r for r in csv.DictReader(f) if r["date"] != day]
        row = {"date": day, **{k: g(k, "price") for k in INSTRUMENTS}, "crack_321": spread["value"]}
        rows.append(row)
        with open(HIST, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: r["date"]))

    print(json.dumps(out, indent=2))
    if status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
