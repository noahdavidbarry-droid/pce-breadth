#!/usr/bin/env python3
"""PCE inflation breadth tracker (Warsh indicator #1).

Pulls BEA NIPA underlying-detail table 2.4.4U (PCE price indexes, monthly)
via the BEA API, computes the share of components with 6-month and 12-month
annualized inflation above 3%, and writes:

  data/latest.json   - newest reading, prior month, change, backtest check
  data/history.csv   - share by month (recomputed each run from current vintage)
  data/lines.json    - catalog of every line in the table (for tuning selection)

Component selection: if components.json exists, only the line numbers it lists
are used (tuned so the July 2026 backtest reproduces Warsh's 54% / 49%).
Otherwise all lines are used and the output is flagged "untuned".

Stdlib only. Needs env BEA_API_KEY.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
THRESHOLD = 3.0
# Warsh's cited figures for July 2026 data (12-month, 6-month), in percent.
BACKTEST = {"month": "2026-07", "share_12m": 54.0, "share_6m": 49.0, "tolerance": 1.5}


def fetch_table(api_key: str) -> list:
    year_now = datetime.now(timezone.utc).year
    years = ",".join(str(y) for y in range(year_now - 3, year_now + 1))
    params = {
        "UserID": api_key,
        "method": "GetData",
        "DatasetName": "NIUnderlyingDetail",
        "TableName": "U20404",
        "Frequency": "M",
        "Year": years,
        "ResultFormat": "JSON",
    }
    url = "https://apps.bea.gov/api/data/?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as resp:
        payload = json.load(resp)
    results = payload.get("BEAAPI", {}).get("Results", {})
    if isinstance(results, list):
        results = results[0]
    if "Error" in results or "Error" in payload.get("BEAAPI", {}):
        raise RuntimeError(f"BEA API error: {results.get('Error') or payload['BEAAPI'].get('Error')}")
    return results["Data"]


def to_month(tp: str) -> str:
    # "2026M07" -> "2026-07"
    y, m = tp.split("M")
    return f"{y}-{int(m):02d}"


def build_series(rows: list):
    series, catalog = {}, {}
    for r in rows:
        line = int(r["LineNumber"])
        catalog[line] = {"line": line, "series_code": r.get("SeriesCode"), "description": r["LineDescription"]}
        val = r.get("DataValue", "").replace(",", "")
        try:
            v = float(val)
        except ValueError:
            continue  # "(NA)", "---" etc.
        series.setdefault(line, {})[to_month(r["TimePeriod"])] = v
    return series, catalog


def shift(month: str, k: int) -> str:
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) - k
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def shares_for(month: str, series: dict, lines: list):
    n = above6 = above12 = 0
    for line in lines:
        s = series.get(line, {})
        cur, p6, p12 = s.get(month), s.get(shift(month, 6)), s.get(shift(month, 12))
        if not cur or not p6 or not p12:
            continue
        n += 1
        if ((cur / p6) ** 2 - 1) * 100 > THRESHOLD:
            above6 += 1
        if (cur / p12 - 1) * 100 > THRESHOLD:
            above12 += 1
    if n == 0:
        return None
    return {"month": month, "components": n,
            "share_6m": round(100 * above6 / n, 1), "share_12m": round(100 * above12 / n, 1)}


def main():
    key = os.environ.get("BEA_API_KEY")
    if not key:
        sys.exit("BEA_API_KEY not set")
    series, catalog = build_series(fetch_table(key))

    sel_path = ROOT / "components.json"
    if sel_path.exists():
        lines = json.loads(sel_path.read_text())["lines"]
        tuned = True
    else:
        lines = sorted(series)
        tuned = False

    months = sorted({m for s in series.values() for m in s})
    history = [h for m in months if (h := shares_for(m, series, lines))]
    if not history:
        sys.exit("No computable months")

    latest, prior = history[-1], (history[-2] if len(history) > 1 else None)
    bt = next((h for h in history if h["month"] == BACKTEST["month"]), None)
    backtest = None
    if bt:
        ok = (abs(bt["share_12m"] - BACKTEST["share_12m"]) <= BACKTEST["tolerance"]
              and abs(bt["share_6m"] - BACKTEST["share_6m"]) <= BACKTEST["tolerance"])
        backtest = {"target": BACKTEST, "computed": bt, "matches": ok}

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "BEA NIPA table 2.4.4U (NIUnderlyingDetail/U20404), monthly",
        "threshold_pct": THRESHOLD,
        "selection_tuned": tuned,
        "latest": latest,
        "prior": prior,
        "change_vs_prior": None if not prior else {
            "share_6m_pp": round(latest["share_6m"] - prior["share_6m"], 1),
            "share_12m_pp": round(latest["share_12m"] - prior["share_12m"], 1),
        },
        "backtest_july_2026": backtest,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "latest.json").write_text(json.dumps(out, indent=2) + "\n")
    (DATA / "lines.json").write_text(json.dumps([catalog[k] for k in sorted(catalog)], indent=2) + "\n")
    with open(DATA / "history.csv", "w") as f:
        f.write("month,components,share_6m,share_12m\n")
        for h in history:
            f.write(f"{h['month']},{h['components']},{h['share_6m']},{h['share_12m']}\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
