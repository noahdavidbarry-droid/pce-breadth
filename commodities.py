"""Daily grain + energy futures snapshot and 3:2:1 crack spread.

Writes data/commodities.json (read by the Warsh indicators email task) and
appends one row per trading day to data/commodities_history.csv.

Sources, per instrument: Stooq (primary, free CSV, no key) -> Yahoo Finance
chart API (fallback). If both fail, the last good value from the previous
commodities.json is kept and marked stale, so the email never goes blank.
Standard library only.
"""
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(DATA, "commodities.json")
HIST = os.path.join(DATA, "commodities_history.csv")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

# key: (label, stooq symbol, yahoo symbol, unit, plausible range in output unit)
INSTRUMENTS = {
    "corn":        ("Corn",        "zc.f", "ZC=F", "$/bu",  (1.5, 15)),
    "soybeans":    ("Soybeans",    "zs.f", "ZS=F", "$/bu",  (4, 30)),
    "wheat":       ("Wheat",       "zw.f", "ZW=F", "$/bu",  (2, 25)),
    "wti":         ("WTI Crude",   "cl.f", "CL=F", "$/bbl", (10, 250)),
    "rbob":        ("RBOB Gasoline", "rb.f", "RB=F", "$/gal", (0.5, 8)),
    "heating_oil": ("Heating Oil", "ho.f", "HO=F", "$/gal", (0.5, 10)),
}
GRAINS = {"corn", "soybeans", "wheat"}


def warn(msg):
    # GitHub Actions annotation (visible in the run summary), plus stderr.
    print(f"::warning::{msg}")
    print(msg, file=sys.stderr)


def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def normalize(key, v):
    """Grain futures are quoted in cents/bu; convert to $/bu."""
    if v is None:
        return None
    v = float(v)
    if key in GRAINS and v > 50:
        v /= 100.0
    return v


def plausible(key, v):
    lo, hi = INSTRUMENTS[key][4]
    return v is not None and lo <= v <= hi


def from_stooq(key):
    sym = INSTRUMENTS[key][1]
    # Daily history gives both last close and previous close.
    try:
        rows = list(csv.DictReader(io.StringIO(get(f"https://stooq.com/q/d/l/?s={sym}&i=d"))))
        rows = [r for r in rows if r.get("Close") not in (None, "", "N/D")]
        if len(rows) < 2:
            warn(f"stooq history {sym}: no usable rows")
        if len(rows) >= 2:
            return {"price": normalize(key, rows[-1]["Close"]),
                    "prev_close": normalize(key, rows[-2]["Close"]),
                    "as_of": rows[-1]["Date"], "source": "stooq"}
    except Exception as e:  # noqa: BLE001
        warn(f"stooq history {sym}: {type(e).__name__}: {e}")
    # Light quote: last price only.
    try:
        rows = list(csv.DictReader(io.StringIO(get(f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv"))))
        if rows and rows[0].get("Close") not in (None, "", "N/D"):
            return {"price": normalize(key, rows[0]["Close"]), "prev_close": None,
                    "as_of": rows[0].get("Date"), "source": "stooq"}
    except Exception as e:  # noqa: BLE001
        warn(f"stooq quote {sym}: {type(e).__name__}: {e}")
    return None


def from_yahoo(key):
    sym = INSTRUMENTS[key][2]
    for host in ("query1", "query2"):
        try:
            j = json.loads(get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"))
            res = j["chart"]["result"][0]
            meta = res["meta"]
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
            price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
            prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
            ts = meta.get("regularMarketTime")
            as_of = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else None
            return {"price": normalize(key, price), "prev_close": normalize(key, prev),
                    "as_of": as_of, "source": "yahoo"}
        except Exception as e:  # noqa: BLE001
            warn(f"yahoo {host} {sym}: {type(e).__name__}: {e}")
    return None


def fetch(key, previous):
    primary = from_stooq(key)
    fallback = None
    if not primary or not plausible(key, primary["price"]):
        primary = None
        fallback = from_yahoo(key)
    q = primary or (fallback if fallback and plausible(key, fallback["price"]) else None)
    if q and q["prev_close"] is None:
        # fill previous close from Yahoo, else from our own last good value
        y = fallback or from_yahoo(key)
        if y and plausible(key, y.get("prev_close")):
            q["prev_close"] = y["prev_close"]
        elif previous and previous.get("price") and previous.get("as_of") != q["as_of"]:
            q["prev_close"] = previous["price"]
    if q:
        q["stale"] = False
        return q
    if previous and previous.get("price") is not None:
        p = dict(previous)
        p["stale"] = True
        return p
    return None


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

    inst = {}
    for key, (label, _, _, unit, _) in INSTRUMENTS.items():
        q = fetch(key, previous.get(key))
        if not q:
            inst[key] = {"label": label, "unit": unit, "price": None, "stale": True, "source": None}
            continue
        chg = None
        if q.get("prev_close"):
            chg = round((q["price"] / q["prev_close"] - 1) * 100, 2)
        inst[key] = {"label": label, "unit": unit,
                     "price": round(q["price"], 4),
                     "prev_close": round(q["prev_close"], 4) if q.get("prev_close") else None,
                     "change_pct": chg, "as_of": q.get("as_of"),
                     "source": q.get("source"), "stale": q["stale"]}

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
