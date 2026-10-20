"""Hyperscaler capex and its second derivative (Warsh indicator #2).

Pulls quarterly purchases of property & equipment from SEC EDGAR's XBRL
company-facts API (free, no key) for Microsoft, Alphabet, Amazon, Meta and
Oracle. Cash-flow figures in 10-Qs are year-to-date, so discrete quarters are
derived by differencing within each fiscal year.

For each company and for the group it computes, per quarter:
  capex, YoY growth, QoQ growth, and the change in YoY growth vs the prior
  quarter (the "second derivative": positive = accelerating).
Also trailing-four-quarter (TTM) totals and growth.

Writes data/capex.json and data/capex_history.csv. Stdlib only.
Note: EDGAR XBRL arrives with the 10-Q/10-K, usually a few days after the
earnings release; earnings-day figures and guidance come from the email's
web search.
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(DATA, "capex.json")
HIST = os.path.join(DATA, "capex_history.csv")
# SEC requires a declared User-Agent of the form "Name contact@email". Set the repo
# variable SEC_USER_AGENT (Settings > Secrets and variables > Actions > Variables).
SEC_UA = os.environ.get("SEC_USER_AGENT", "").strip()
UA = {"User-Agent": SEC_UA, "Accept": "application/json"}

COMPANIES = {  # ticker: (name, CIK)
    "MSFT": ("Microsoft", "0000789019"),
    "GOOGL": ("Alphabet", "0001652044"),
    "AMZN": ("Amazon", "0001018724"),
    "META": ("Meta", "0001326801"),
    "ORCL": ("Oracle", "0001341439"),
}
BIG4 = ["MSFT", "GOOGL", "AMZN", "META"]
CONCEPTS = ["PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsForProceedsFromProductiveAssets"]
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}


def warn(msg):
    print(f"::warning::{msg}")
    print(msg, file=sys.stderr)


def d(s):
    return date.fromisoformat(s)


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def quarters_from_facts(entries):
    """entries: XBRL USD facts for one concept -> {quarter_end: value}."""
    best = {}
    for e in entries:
        if e.get("form") not in FORMS or "start" not in e:
            continue
        k = (e["start"], e["end"])
        if k not in best or e.get("filed", "") > best[k].get("filed", ""):
            best[k] = e
    by_start = {}
    for (s, en), e in best.items():
        by_start.setdefault(s, []).append((en, e["val"], e.get("filed")))
    q = {}
    for s, items in by_start.items():
        items.sort()
        prev_end, prev_val = s, 0.0
        for en, val, filed in items:
            span = (d(en) - d(prev_end)).days
            if 75 <= span <= 105:
                direct = prev_end == s          # a true 3-month figure
                if direct or en not in q:
                    q[en] = {"value": val - prev_val, "filed": filed}
            prev_end, prev_val = en, val      # a gap means the next piece can't be differenced
    return q


def company_series(ticker):
    name, cik = COMPANIES[ticker]
    facts = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    gaap = facts.get("facts", {}).get("us-gaap", {})
    best_q, best_concept = {}, None
    for c in CONCEPTS:
        units = gaap.get(c, {}).get("units", {}).get("USD")
        if not units:
            continue
        q = quarters_from_facts(units)
        if q and (not best_q or max(q) > max(best_q) or (max(q) == max(best_q) and len(q) > len(best_q))):
            best_q, best_concept = q, c
    if not best_q:
        raise ValueError("no capex concept found")
    return best_q, best_concept


def cal_quarter(end):
    dt = d(end)
    return f"{dt.year}Q{(dt.month - 1) // 3 + 1}"


def prev_cq(cq, k=1):
    y, qn = int(cq[:4]), int(cq[-1])
    idx = y * 4 + qn - 1 - k
    return f"{idx // 4}Q{idx % 4 + 1}"


def pct(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b else None


def metrics(by_cq):
    """by_cq: {calendar_quarter: value} -> per-quarter metrics list (oldest first)."""
    rows = []
    for cq in sorted(by_cq):
        v = by_cq[cq]
        yoy = pct(v, by_cq.get(prev_cq(cq, 4)))
        qoq = pct(v, by_cq.get(prev_cq(cq, 1)))
        ttm_q = [by_cq.get(prev_cq(cq, k)) for k in range(4)]
        ttm_prev_q = [by_cq.get(prev_cq(cq, k)) for k in range(4, 8)]
        ttm = sum(ttm_q) if None not in ttm_q else None
        ttm_prev = sum(ttm_prev_q) if None not in ttm_prev_q else None
        rows.append({"quarter": cq, "capex_bn": round(v / 1e9, 2), "yoy_pct": yoy, "qoq_pct": qoq,
                     "ttm_bn": round(ttm / 1e9, 1) if ttm else None, "ttm_yoy_pct": pct(ttm, ttm_prev)})
    for i, r in enumerate(rows):
        p = rows[i - 1] if i and rows[i - 1]["quarter"] == prev_cq(r["quarter"]) else None
        r["yoy_change_pp"] = (round(r["yoy_pct"] - p["yoy_pct"], 1)
                              if p and r["yoy_pct"] is not None and p["yoy_pct"] is not None else None)
        r["ttm_yoy_change_pp"] = (round(r["ttm_yoy_pct"] - p["ttm_yoy_pct"], 1)
                                  if p and r["ttm_yoy_pct"] is not None and p["ttm_yoy_pct"] is not None else None)
        if r["yoy_change_pp"] is not None:
            r["second_derivative"] = ("accelerating" if r["yoy_change_pp"] > 0
                                      else "decelerating" if r["yoy_change_pp"] < 0 else "flat")
    return rows


def peak(rows, field="yoy_pct"):
    vals = [r for r in rows if r.get(field) is not None]
    if not vals:
        return None
    top = max(vals, key=lambda r: r[field])
    return {"quarter": top["quarter"], field: top[field],
            "quarters_since_peak": len(vals) - 1 - vals.index(top)}


def main():
    if "@" not in SEC_UA:
        warn("capex: SEC_USER_AGENT repo variable not set (needs 'Name email@domain'); skipping")
        return
    previous = {}
    if os.path.exists(OUT):
        try:
            previous = json.load(open(OUT))
        except Exception:  # noqa: BLE001
            previous = {}

    comp, raw_cq, errors = {}, {}, {}
    for i, (t, (name, _)) in enumerate(COMPANIES.items()):
        if i:
            time.sleep(1.0)  # stay far below SEC's 10 requests/second limit
        try:
            q, concept = company_series(t)
        except Exception as e:  # noqa: BLE001
            warn(f"edgar {t}: {type(e).__name__}: {e}")
            errors[t] = str(e)
            if previous.get("companies", {}).get(t):
                comp[t] = dict(previous["companies"][t], stale=True)
                raw_cq[t] = {r["quarter"]: r["capex_bn"] * 1e9 for r in comp[t].get("quarters", [])}
            continue
        by_cq = {}
        for end, v in q.items():
            by_cq[cal_quarter(end)] = v["value"]
        raw_cq[t] = by_cq
        rows = metrics(by_cq)[-12:]
        last_end = max(q)
        prev_latest = previous.get("companies", {}).get(t, {}).get("latest_quarter")
        comp[t] = {"name": name, "concept": concept, "latest_quarter": rows[-1]["quarter"],
                   "latest_period_end": last_end, "latest_filed": q[last_end]["filed"],
                   "new_since_last_run": bool(prev_latest and prev_latest != rows[-1]["quarter"]),
                   "latest": rows[-1], "yoy_peak_last_12q": peak(rows), "quarters": rows, "stale": False}

    def group(tickers, label):
        have = [t for t in tickers if t in raw_cq]
        cqs = sorted(set.intersection(*[set(raw_cq[t]) for t in have])) if have else []
        by_cq = {cq: sum(raw_cq[t][cq] for t in have) for cq in cqs}
        rows = metrics(by_cq)[-12:]
        all_q = sorted(set().union(*[set(raw_cq[t]) for t in have])) if have else []
        newest = all_q[-1] if all_q else None
        reported = [t for t in have if newest in raw_cq[t]]
        return {"label": label, "members": have, "quarters": rows,
                "latest_complete_quarter": rows[-1]["quarter"] if rows else None,
                "latest": rows[-1] if rows else None, "yoy_peak_last_12q": peak(rows),
                "ttm_yoy_peak_last_12q": peak(rows, "ttm_yoy_pct"),
                "newest_quarter": newest, "newest_quarter_reported_by": reported,
                "newest_quarter_pending": [t for t in have if t not in reported]}

    groups = {"big4": group(BIG4, "Microsoft + Alphabet + Amazon + Meta"),
              "big4_plus_oracle": group(list(COMPANIES), "Big 4 + Oracle")}

    out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": "SEC EDGAR XBRL company facts (10-Q/10-K cash flow: purchases of property & equipment)",
           "status": "ok" if not errors else ("partial" if len(errors) < len(COMPANIES) else "failed"),
           "errors": errors,
           "notes": ["Quarters are calendar quarters of the fiscal period end (Oracle's quarters end in "
                     "Feb/May/Aug/Nov, so they are offset by a month).",
                     "Capex = cash purchases of property and equipment; excludes finance leases "
                     "(material for Microsoft and Meta) and includes non-AI spending (e.g. Amazon logistics).",
                     "second_derivative compares this quarter's YoY growth with last quarter's: "
                     "positive yoy_change_pp = capex growth accelerating."],
           "groups": groups, "companies": comp}
    if errors and len(errors) == len(COMPANIES):
        print(json.dumps(out, indent=2))
        sys.exit(1)
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
    with open(HIST, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity", "quarter", "capex_bn", "yoy_pct", "yoy_change_pp", "ttm_bn", "ttm_yoy_pct"])
        for name, blk in list(groups.items()) + list(comp.items()):
            for r in blk.get("quarters", []):
                w.writerow([name, r["quarter"], r["capex_bn"], r["yoy_pct"], r.get("yoy_change_pp"),
                            r["ttm_bn"], r["ttm_yoy_pct"]])
    print(json.dumps({k: v for k, v in out.items() if k != "companies"}, indent=2)[:4000])


if __name__ == "__main__":
    main()
