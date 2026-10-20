"""Drill-down for the PCE breadth indicator.

Given the monthly price-index series for every leaf component and BEA's nominal
spending (table 2.4.5U) for the latest month, computes:
  - shares above 3% on 3-, 6- and 12-month annualized bases
  - spending-weighted versions of those shares
  - the same split by goods / services and core / food / energy
  - which components crossed the 3% line since the prior month
  - the heaviest components currently running above 3%
Writes data/breadth_detail.json. Imported by pce_breadth.py; stdlib only.
"""
import json
from pathlib import Path

THRESHOLD = 3.0
WINDOWS = {"3m": (3, 4), "6m": (6, 2), "12m": (12, 1)}  # months back, annualizing power


def shift(month, k):
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) - k
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def rate(series, line, month, window):
    back, power = WINDOWS[window]
    s = series.get(line, {})
    cur, base = s.get(month), s.get(shift(month, back))
    if not cur or not base:
        return None
    return ((cur / base) ** power - 1) * 100


def subtree_leaves(agg_line, leaves, nominal_vals):
    """Leaves under an aggregate: the leaves following it whose spending sums to its total."""
    target = nominal_vals.get(agg_line)
    if not target:
        return []
    out, s = [], 0.0
    for ln in leaves:
        if ln <= agg_line:
            continue
        v = nominal_vals.get(ln) or 0.0
        out.append(ln)
        s += v
        if abs(s - target) <= max(2.0, 0.0005 * target):
            return out
        if s > target * 1.001:
            break
    return []


def find_line(descs, *starts):
    for ln in sorted(descs):
        d = (descs[ln] or "").lower()
        if any(d.startswith(st) for st in starts):
            return ln
    return None


def classify(leaves, descs, nominal_vals):
    goods_line = find_line(descs, "goods")
    services_line = find_line(descs, "services")
    food = set(subtree_leaves(find_line(descs, "food and beverages purchased for off-premises"), leaves, nominal_vals))
    energy = set(subtree_leaves(find_line(descs, "gasoline and other energy goods"), leaves, nominal_vals))
    energy |= set(subtree_leaves(find_line(descs, "electricity and gas"), leaves, nominal_vals))
    groups = {}
    for ln in leaves:
        g = {"goods" if (services_line and ln < services_line and goods_line and ln > goods_line) else "services"}
        g.add("food" if ln in food else "energy" if ln in energy else "core")
        groups[ln] = g
    checks = {"food_leaves": len(food), "energy_leaves": len(energy),
              "goods_line": goods_line, "services_line": services_line}
    return groups, checks


def share_block(lines, month, series, weights, window):
    n = above = 0
    w_tot = w_above = 0.0
    for ln in lines:
        r = rate(series, ln, month, window)
        if r is None:
            continue
        n += 1
        w = weights.get(ln, 0.0)
        w_tot += w
        if r > THRESHOLD:
            above += 1
            w_above += w
    if n == 0:
        return None
    return {"components": n, "above": above, "share": round(100 * above / n, 1),
            "weighted_share": round(100 * w_above / w_tot, 1) if w_tot else None}


def build(series, leaves, descs, nominal_vals, history, latest_month, prior_month, data_dir):
    weights = {ln: abs(nominal_vals.get(ln) or 0.0) for ln in leaves}
    total_w = sum(weights.values()) or 1.0
    groups, checks = classify(leaves, descs, nominal_vals)
    sets = {
        "all": leaves,
        "goods": [l for l in leaves if "goods" in groups[l]],
        "services": [l for l in leaves if "services" in groups[l]],
        "core": [l for l in leaves if "core" in groups[l]],
        "food": [l for l in leaves if "food" in groups[l]],
        "energy": [l for l in leaves if "energy" in groups[l]],
    }

    shares = {}
    for name, ls in sets.items():
        shares[name] = {}
        for w in WINDOWS:
            cur = share_block(ls, latest_month, series, weights, w)
            prv = share_block(ls, prior_month, series, weights, w) if prior_month else None
            if cur:
                cur["change_pp"] = round(cur["share"] - prv["share"], 1) if prv else None
                cur["weighted_change_pp"] = (round(cur["weighted_share"] - prv["weighted_share"], 1)
                                             if prv and cur["weighted_share"] is not None
                                             and prv["weighted_share"] is not None else None)
            shares[name][w] = cur

    def item(ln, window):
        return {"line": ln, "description": descs.get(ln),
                "group": "/".join(sorted(groups[ln])),
                "weight_pct": round(100 * weights[ln] / total_w, 2),
                "rate_now": round(rate(series, ln, latest_month, window), 1),
                "rate_prior": (round(rate(series, ln, prior_month, window), 1)
                               if prior_month and rate(series, ln, prior_month, window) is not None else None)}

    crossings = {}
    for w in ("6m", "12m"):
        entered, exited = [], []
        for ln in leaves:
            now, before = rate(series, ln, latest_month, w), rate(series, ln, prior_month, w) if prior_month else None
            if now is None or before is None:
                continue
            if now > THRESHOLD >= before:
                entered.append(item(ln, w))
            elif before > THRESHOLD >= now:
                exited.append(item(ln, w))
        entered.sort(key=lambda x: -x["weight_pct"])
        exited.sort(key=lambda x: -x["weight_pct"])
        crossings[w] = {"entered_count": len(entered), "exited_count": len(exited),
                        "entered_weight_pct": round(sum(x["weight_pct"] for x in entered), 1),
                        "exited_weight_pct": round(sum(x["weight_pct"] for x in exited), 1),
                        "entered": entered[:12], "exited": exited[:12]}

    hot = [item(ln, "12m") for ln in leaves
           if (r := rate(series, ln, latest_month, "12m")) is not None and r > THRESHOLD]
    hot.sort(key=lambda x: -x["weight_pct"])

    out = {
        "month": latest_month, "prior_month": prior_month, "threshold_pct": THRESHOLD,
        "weights_note": "Spending weights are each component's nominal PCE (BEA table 2.4.5U) "
                        "in the latest month; the same weights are applied to the prior month.",
        "groups_note": "Core = excluding food (off-premises) and energy goods and services, "
                       "matching BEA's 'PCE excluding food and energy'.",
        "group_check": checks,
        "shares": shares,
        "crossings_vs_prior": crossings,
        "heaviest_above_3pct_12m": hot[:10],
        "trend_last_13_months": history[-13:],
    }
    (Path(data_dir) / "breadth_detail.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def weighted_history_fields(series, leaves, nominal_vals, month):
    """Extra history columns: 3m share and spending-weighted 6m/12m shares."""
    weights = {ln: abs(nominal_vals.get(ln) or 0.0) for ln in leaves}
    out = {}
    for w, key in (("3m", "share_3m"),):
        b = share_block(leaves, month, series, weights, w)
        out[key] = b["share"] if b else None
    for w in ("6m", "12m"):
        b = share_block(leaves, month, series, weights, w)
        out[f"wshare_{w}"] = b["weighted_share"] if b else None
    return out
