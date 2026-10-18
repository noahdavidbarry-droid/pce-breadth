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


def fetch_table(api_key: str, table: str = "U20404", years_back: int = 3) -> list:
    year_now = datetime.now(timezone.utc).year
    years = ",".join(str(y) for y in range(year_now - years_back, year_now + 1))
    params = {
        "UserID": api_key,
        "method": "GetData",
        "DatasetName": "NIUnderlyingDetail",
        "TableName": table,
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


def find_leaves(nominal: dict, order: list, month: str, descs: dict = None):
    """Identify the most granular components using nominal spending (table 2.4.5U).

    In current dollars an aggregate equals the sum of its direct children, and
    children follow their parent in table order. A line is treated as an
    aggregate if the subtrees immediately after it sum to its value; otherwise
    it is a leaf. Lines after the root's subtree (addenda) are excluded.
    """
    descs = descs or {}
    raw = [nominal.get(ln, {}).get(month) for ln in order]
    # "Less:" lines are subtracted from their parent.
    sign = [-1 if str(descs.get(ln, "")).strip().lower().startswith("less") else 1 for ln in order]
    vals = raw
    n = len(order)
    memo = {}

    def parse(i):  # returns (end_exclusive, is_aggregate)
        if i in memo:
            return memo[i]
        v = vals[i]
        result = (i + 1, False)
        if v is not None and abs(v) > 0:
            s, k, kids = 0.0, i + 1, 0
            while k < n and kids < 80 and vals[k] is not None:
                e, _ = parse(k)
                s += sign[k] * vals[k]
                kids += 1
                k = e
                tol = max(1.0, 0.0005 * abs(v)) + 0.5 * kids
                if abs(s - v) <= tol and kids >= 2 or (kids == 1 and abs(s - v) <= 0.5):
                    result = (k, True)
                    break
                # Stop once past the target, unless the next line would subtract.
                nxt_neg = k < n and vals[k] is not None and sign[k] * vals[k] < 0
                if s > v + tol and not nxt_neg:
                    break
        memo[i] = result
        return result

    sys.setrecursionlimit(10000)
    end, is_agg = parse(0)
    leaves = []

    def collect(i, stop):
        while i < stop:
            e, agg = parse(i)
            if agg:
                collect(i + 1, e)
            else:
                leaves.append(order[i])
            i = e

    if is_agg:
        collect(1, end)
    return leaves, end


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
    selection_note = None
    if sel_path.exists():
        lines = json.loads(sel_path.read_text())["lines"]
        tuned = "components.json"
    else:
        nominal, ncat = build_series(fetch_table(key, "U20405", years_back=1))
        order = sorted(set(ncat) | set(catalog))
        descs = {ln: (ncat.get(ln) or catalog.get(ln))["description"] for ln in order}
        nmonths = sorted({m for s in nominal.values() for m in s})
        DATA.mkdir(exist_ok=True)
        (DATA / "nominal_latest.json").write_text(json.dumps(
            [{"line": ln, "description": ncat.get(ln, {}).get("description"),
              "value": nominal.get(ln, {}).get(nmonths[-1]) if nmonths else None}
             for ln in sorted(set(ncat) | set(order))], indent=1) + "\n")
        found, root_end = find_leaves(nominal, order, nmonths[-1], descs) if nmonths else ([], 0)
        # Nonprofit (NPISH) detail is gross output and sales, not household spending
        # categories: count "Final consumption expenditures of NPISHs" as one component.
        npish = [ln for ln in order if descs[ln].lower().startswith("final consumption expenditures of nonprofit")]
        if npish and found:
            found = [ln for ln in found if ln < npish[0]] + [npish[0]]
        lines = found
        if len(lines) >= 50:
            tuned = "leaf components (aggregates excluded via 2.4.5U nominal sums)"
            selection_note = {"leaf_lines": len(lines), "lines_in_tree": root_end,
                              "excluded_addenda": len(order) - root_end}
            (DATA.mkdir(exist_ok=True))
            lines = [ln for ln in lines if ln in series]  # leaves that have a price index
            selection_note["leaf_lines_with_price_index"] = len(lines)
            (DATA / "leaves.json").write_text(json.dumps(
                [catalog[ln] for ln in lines], indent=2) + "\n")
        else:
            lines = sorted(series)
            tuned = False
            selection_note = {"warning": f"leaf detection found only {len(found)} lines; using all lines"}

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
        "selection": tuned,
        "selection_detail": selection_note,
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
