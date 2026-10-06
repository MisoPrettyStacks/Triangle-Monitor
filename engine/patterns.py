"""Pattern detection on hourly candles.

Detects: descending / ascending / symmetrical triangles, bull & bear flags,
double tops & bottoms, confirmed breakouts, and computes measured-move exits.
All geometry is returned in plottable form (lines as point series).
"""
from __future__ import annotations

import math


# ---------------------------------------------------------------- helpers

def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Return (slope, intercept, r_squared) for y = slope*x + intercept."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return 0.0, my, 0.0
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (sxy * sxy / (sxx * syy)) if (sxx and syy) else 0.0
    return slope, intercept, r2


def swing_highs(cs: list[dict], k: int = 2) -> list[int]:
    idx = []
    for i in range(k, len(cs) - k):
        h = cs[i]["h"]
        if all(h >= cs[j]["h"] for j in range(i - k, i + k + 1) if j != i):
            idx.append(i)
    return idx


def swing_lows(cs: list[dict], k: int = 2) -> list[int]:
    idx = []
    for i in range(k, len(cs) - k):
        lo = cs[i]["l"]
        if all(lo <= cs[j]["l"] for j in range(i - k, i + k + 1) if j != i):
            idx.append(i)
    return idx


def line_points(slope: float, intercept: float, t0: int, t1: int,
                n: int = 24) -> list[list]:
    """Sample a line y=slope*t+intercept as [[t, price], ...] for plotting."""
    pts = []
    for i in range(n):
        t = t0 + (t1 - t0) * i / max(n - 1, 1)
        pts.append([int(t), slope * t + intercept])
    return pts


def _find_support(cs: list[dict], band_pct: float = 1.2,
                  min_touches: int = 4, viol_pct: float = 3.0):
    """Find a flat support band. Returns dict or None."""
    win = cs[-120:]  # last ~5 days
    lows = sorted((c["l"], c["t"]) for c in win)
    if len(lows) < min_touches:
        return None
    # candidate mid = median of the lowest 12 lows
    cand = sorted(p for p, _ in lows[:12])
    mid = cand[len(cand) // 2]
    tol = mid * band_pct / 100.0
    touches = [(p, t) for p, t in lows if abs(p - mid) <= tol]
    if len(touches) < min_touches:
        return None
    # must span at least ~36h
    ts = [t for _, t in touches]
    if max(ts) - min(ts) < 36 * 3600 * 1000:
        return None
    # no violation: no low more than viol_pct below mid in window
    if min(p for p, _ in lows) < mid * (1 - viol_pct / 100.0):
        return None
    return {"level": mid, "touches": len(touches),
            "band": [mid - tol, mid + tol]}


def _find_resistance(cs: list[dict], band_pct: float = 1.2,
                     min_touches: int = 4):
    """Flat resistance band (mirror of support)."""
    win = cs[-120:]
    highs = sorted((c["h"], c["t"]) for c in win)
    if len(highs) < min_touches:
        return None
    cand = sorted((p for p, _ in highs[-12:]), reverse=True)
    mid = cand[len(cand) // 2]
    tol = mid * band_pct / 100.0
    touches = [(p, t) for p, t in highs if abs(p - mid) <= tol]
    if len(touches) < min_touches:
        return None
    ts = [t for _, t in touches]
    if max(ts) - min(ts) < 36 * 3600 * 1000:
        return None
    return {"level": mid, "touches": len(touches),
            "band": [mid - tol, mid + tol]}


def _descending_line(cs: list[dict], min_r2: float = 0.70):
    """Fit a descending trendline through successive lower swing highs.

    Uses the running-minimum envelope of swing highs so local noise can't
    break the descending sequence.
    """
    shi = swing_highs(cs, k=2)
    shi = [i for i in shi if i >= len(cs) - 120]
    if len(shi) < 3:
        return None
    # running-minimum envelope: keep a high only if it undercuts all prior
    env = []
    run_min = math.inf
    for i in shi:
        h = cs[i]["h"]
        if h < run_min:
            run_min = h
            env.append(i)
    if len(env) < 3:
        return None
    # most recent swing high should be reasonably fresh (< 60 bars old).
    # note: freshness is judged on the last swing high, not the last
    # envelope anchor — near a triangle's apex the swings compress and stop
    # printing new running-minimum extremes, which is the coil itself, not
    # a stale pattern.
    if shi[-1] < len(cs) - 60:
        return None
    hs = [cs[i]["h"] for i in env]
    xs = [float(cs[i]["t"]) for i in env]
    slope, intercept, r2 = _linreg(xs, hs)
    if slope >= 0 or r2 < min_r2:
        return None
    return {"slope": slope, "intercept": intercept, "r2": r2,
            "anchors": [[cs[i]["t"], cs[i]["h"]] for i in env],
            "first_high": hs[0]}


def _ascending_line(cs: list[dict], min_r2: float = 0.70):
    """Fit an ascending trendline through successive higher swing lows."""
    sli = swing_lows(cs, k=2)
    sli = [i for i in sli if i >= len(cs) - 120]
    if len(sli) < 3:
        return None
    env = []
    run_max = -math.inf
    for i in sli:
        lo = cs[i]["l"]
        if lo > run_max:
            run_max = lo
            env.append(i)
    if len(env) < 3:
        return None
    # freshness judged on the last swing low, not the last envelope anchor —
    # near a triangle's apex the swings compress and stop printing new
    # running-maximum extremes, which is the coil itself, not a stale pattern.
    if sli[-1] < len(cs) - 60:
        return None
    ls = [cs[i]["l"] for i in env]
    xs = [float(cs[i]["t"]) for i in env]
    slope, intercept, r2 = _linreg(xs, ls)
    if slope <= 0 or r2 < min_r2:
        return None
    return {"slope": slope, "intercept": intercept, "r2": r2,
            "anchors": [[cs[i]["t"], cs[i]["l"]] for i in env],
            "first_low": ls[0]}


def _line_value(line, t: int) -> float:
    return line["slope"] * t + line["intercept"]


# ------------------------------------------------------------ detectors

LABELS = {
    "descending_triangle": "Descending Triangle Pattern",
    "ascending_triangle": "Ascending Triangle Pattern",
    "symmetrical_triangle": "Symmetrical Triangle Pattern",
    "bull_flag": "Bull Flag",
    "bear_flag": "Bear Flag",
    "double_bottom": "Double Bottom",
    "double_top": "Double Top",
}


def _descending_geometry(cs: list[dict], cfg: dict) -> dict | None:
    """Shared geometry for descending triangles (forming or just broken)."""
    d = cfg.get("detection", {})
    sup = _find_support(cs, d.get("support_band_pct", 1.2),
                        d.get("min_support_touches", 4),
                        d.get("support_violation_pct", 3.0))
    if not sup:
        return None
    res = _descending_line(cs, d.get("trendline_min_r2", 0.70))
    if not res:
        return None
    last = cs[-1]
    t_now = last["t"]
    line_now = _line_value(res, t_now)
    height = res["first_high"] - sup["level"]
    if height <= 0 or line_now <= sup["level"]:
        return None
    coil = (line_now - sup["level"]) / height
    if coil > d.get("coil_max_ratio", 0.60):
        return None
    t_apex = int((sup["level"] - res["intercept"]) / res["slope"])
    t_start = cs[-120]["t"]
    apex_compression = (t_apex - t_now) < max((t_apex - t_start) / 3.0, 1)
    v1 = sum(c["v"] for c in cs[-20:]) / 20
    v2 = sum(c["v"] for c in cs[-40:-20]) / 20
    vol_contracting = v1 < v2 if v2 else False

    quality = 50 + min(sup["touches"], 10) * 3 + res["r2"] * 20
    if apex_compression:
        quality += 10
    if vol_contracting:
        quality += 5

    t0, t1 = cs[-120]["t"], max(t_apex, t_now + 12 * 3600 * 1000)
    return {
        "type": "descending_triangle",
        "label": LABELS["descending_triangle"],
        "direction": "long",
        "support_level": sup["level"],
        "support_band": sup["band"],
        "support_touches": sup["touches"],
        "resistance_line": line_points(res["slope"], res["intercept"], t0, t1),
        "resistance_anchors": res["anchors"],
        "resistance_r2": round(res["r2"], 3),
        "resistance_now": line_now,
        "_res_slope": res["slope"],
        "_res_intercept": res["intercept"],
        "apex": [t_apex, sup["level"]],
        "apex_compression": bool(apex_compression),
        "coil_ratio": round(coil, 3),
        "height": height,
        "vol_contracting": bool(vol_contracting),
        "breakout_level": line_now,
        "invalidation_level": sup["band"][0],
        "quality": round(min(quality, 99), 1),
    }


def detect_descending(cs: list[dict], cfg: dict) -> dict | None:
    g = _descending_geometry(cs, cfg)
    if not g:
        return None
    last = cs[-1]
    # price must still be inside: below line, above support
    if not (g["support_level"] < last["c"] < g["resistance_now"]):
        return None
    return g


def detect_fresh_breakout(cs: list[dict], cfg: dict,
                          lookback: int = 3) -> dict | None:
    """Catch a triangle whose breakout already happened in the last few bars
    (cold start: the app never saw it forming)."""
    for geo_fn, lvl_key, is_long in (
        (_descending_geometry, "resistance_now", True),
    ):
        g = geo_fn(cs, cfg)
        if not g:
            continue
        slope, intercept = g["_res_slope"], g["_res_intercept"]
        for c in cs[-lookback:]:
            line_at = slope * c["t"] + intercept
            if is_long and c["c"] > line_at and c["l"] < line_at:
                # genuine cross: bar straddled the line and closed above
                g["already_broken"] = True
                g["breakout_bar_t"] = c["t"]
                g["breakout_level"] = line_at
                return g
    return None


def detect_ascending(cs: list[dict], cfg: dict) -> dict | None:
    d = cfg.get("detection", {})
    res = _find_resistance(cs, d.get("support_band_pct", 1.2),
                           d.get("min_support_touches", 4))
    if not res:
        return None
    asc = _ascending_line(cs, d.get("trendline_min_r2", 0.70))
    if not asc:
        return None
    last = cs[-1]
    t_now = last["t"]
    line_now = _line_value(asc, t_now)
    if not (line_now < last["c"] < res["level"]):
        return None
    height = res["level"] - asc["first_low"]
    if height <= 0:
        return None
    coil = (res["level"] - line_now) / height
    if coil > d.get("coil_max_ratio", 0.60):
        return None
    t_apex = int((res["level"] - asc["intercept"]) / asc["slope"])
    t_start = cs[-120]["t"]
    apex_compression = (t_apex - t_now) < max((t_apex - t_start) / 3.0, 1)
    v1 = sum(c["v"] for c in cs[-20:]) / 20
    v2 = sum(c["v"] for c in cs[-40:-20]) / 20
    vol_contracting = v1 < v2 if v2 else False
    quality = 50 + min(res["touches"], 10) * 3 + asc["r2"] * 20
    if apex_compression:
        quality += 10
    if vol_contracting:
        quality += 5
    t0, t1 = cs[-120]["t"], max(t_apex, t_now + 12 * 3600 * 1000)
    return {
        "type": "ascending_triangle",
        "label": LABELS["ascending_triangle"],
        "direction": "long",
        "resistance_level": res["level"],
        "resistance_band": res["band"],
        "resistance_touches": res["touches"],
        "support_line": line_points(asc["slope"], asc["intercept"], t0, t1),
        "support_anchors": asc["anchors"],
        "support_r2": round(asc["r2"], 3),
        "support_now": line_now,
        "apex": [t_apex, res["level"]],
        "apex_compression": bool(apex_compression),
        "coil_ratio": round(coil, 3),
        "height": height,
        "vol_contracting": bool(vol_contracting),
        "breakout_level": res["level"],
        "invalidation_level": line_now,
        "quality": round(min(quality, 99), 1),
    }


def detect_symmetrical(cs: list[dict], cfg: dict) -> dict | None:
    d = cfg.get("detection", {})
    res = _descending_line(cs, d.get("trendline_min_r2", 0.70))
    asc = _ascending_line(cs, d.get("trendline_min_r2", 0.70))
    if not (res and asc):
        return None
    last = cs[-1]
    t_now = last["t"]
    r_now = _line_value(res, t_now)
    s_now = _line_value(asc, t_now)
    # price should still be coiling between the lines. near the apex the two
    # regression lines can cross by a hair, so allow a small tolerance (0.2%)
    # rather than demanding the close sit strictly inside.
    tol = last["c"] * 0.002
    if not (s_now - tol < last["c"] < r_now + tol):
        return None
    height = res["first_high"] - asc["first_low"]
    if height <= 0:
        return None
    coil = (r_now - s_now) / height
    if coil > d.get("coil_max_ratio", 0.60):
        return None
    denom = res["slope"] - asc["slope"]
    if denom == 0:
        return None
    t_apex = int((asc["intercept"] - res["intercept"]) / denom)
    apex_price = _line_value(res, t_apex)
    t_start = cs[-120]["t"]
    apex_compression = (t_apex - t_now) < max((t_apex - t_start) / 3.0, 1)
    # direction: whichever line price is closer to pressing
    direction = "long" if (r_now - last["c"]) <= (last["c"] - s_now) else "short"
    t0, t1 = cs[-120]["t"], max(t_apex, t_now + 12 * 3600 * 1000)
    quality = 50 + (res["r2"] + asc["r2"]) * 15 + (10 if apex_compression else 0)
    return {
        "type": "symmetrical_triangle",
        "label": LABELS["symmetrical_triangle"],
        "direction": direction,
        "resistance_line": line_points(res["slope"], res["intercept"], t0, t1),
        "resistance_anchors": res["anchors"],
        "support_line": line_points(asc["slope"], asc["intercept"], t0, t1),
        "support_anchors": asc["anchors"],
        "resistance_now": r_now,
        "support_now": s_now,
        "apex": [t_apex, apex_price],
        "apex_compression": bool(apex_compression),
        "coil_ratio": round(coil, 3),
        "height": height,
        "breakout_level": r_now if direction == "long" else s_now,
        "invalidation_level": s_now if direction == "long" else r_now,
        "quality": round(min(quality, 99), 1),
    }


def detect_flag(cs: list[dict], bullish: bool = True) -> dict | None:
    """Impulse + tight drift with declining volume."""
    if len(cs) < 40:
        return None
    # impulse: biggest 12-bar move in the last 60 bars
    best = None
    for i in range(len(cs) - 60, len(cs) - 12):
        move = (cs[i + 12]["c"] - cs[i]["c"]) / cs[i]["c"]
        if bullish and move > 0.08 and (best is None or move > best[0]):
            best = (move, i)
        if not bullish and move < -0.08 and (best is None or move < best[0]):
            best = (move, i)
    if not best:
        return None
    _, i0 = best
    flag = cs[i0 + 12:]
    if len(flag) < 8:
        return None
    hi = max(c["h"] for c in flag)
    lo = min(c["l"] for c in flag)
    impulse_range = abs(cs[i0 + 12]["c"] - cs[i0]["c"])
    if impulse_range == 0 or (hi - lo) / impulse_range > 0.45:
        return None
    # drift should be flat-to-countertrend
    slope, _, _ = _linreg([float(c["t"]) for c in flag],
                          [c["c"] for c in flag])
    if bullish and slope > 0 and slope * 3600e3 * len(flag) > impulse_range * 0.3:
        return None
    if not bullish and slope < 0 and abs(slope) * 3600e3 * len(flag) > impulse_range * 0.3:
        return None
    v1 = sum(c["v"] for c in flag[-8:]) / 8
    v2 = sum(c["v"] for c in cs[i0:i0 + 12]) / 12
    if not (v2 and v1 < v2 * 0.8):
        return None
    last = cs[-1]
    top, bot = (hi, lo) if bullish else (hi, lo)
    trig = top if bullish else bot
    inside = (lo < last["c"] < hi)
    if not inside:
        return None
    return {
        "type": "bull_flag" if bullish else "bear_flag",
        "label": LABELS["bull_flag"] if bullish else LABELS["bear_flag"],
        "direction": "long" if bullish else "short",
        "flag_top": hi,
        "flag_bottom": lo,
        "impulse_pct": round(abs(best[0]) * 100, 1),
        "breakout_level": trig,
        "invalidation_level": bot if bullish else top,
        "height": hi - lo,
        "quality": 70.0,
    }


def detect_double(cs: list[dict], bottom: bool = True) -> dict | None:
    win = cs[-120:]
    if bottom:
        ext = sorted(((c["l"], i) for i, c in enumerate(win)), key=lambda x: x[0])[:2]
        i1, i2 = sorted(i for _, i in ext)
        if i2 - i1 < 12:
            return None
        p1, p2 = win[i1]["l"], win[i2]["l"]
        if abs(p1 - p2) / p1 > 0.015:
            return None
        neck = max(c["h"] for c in win[i1:i2])
        last = cs[-1]
        if not (max(p1, p2) < last["c"] < neck):
            return None
        return {
            "type": "double_bottom",
            "label": LABELS["double_bottom"],
            "direction": "long",
            "neckline": neck,
            "lows": [p1, p2],
            "breakout_level": neck,
            "invalidation_level": min(p1, p2),
            "height": neck - (p1 + p2) / 2,
            "quality": 65.0,
        }
    else:
        ext = sorted(((c["h"], i) for i, c in enumerate(win)),
                     key=lambda x: -x[0])[:2]
        i1, i2 = sorted(i for _, i in ext)
        if i2 - i1 < 12:
            return None
        p1, p2 = win[i1]["h"], win[i2]["h"]
        if abs(p1 - p2) / p1 > 0.015:
            return None
        neck = min(c["l"] for c in win[i1:i2])
        last = cs[-1]
        if not (neck < last["c"] < min(p1, p2)):
            return None
        return {
            "type": "double_top",
            "label": LABELS["double_top"],
            "direction": "short",
            "neckline": neck,
            "highs": [p1, p2],
            "breakout_level": neck,
            "invalidation_level": max(p1, p2),
            "height": (p1 + p2) / 2 - neck,
            "quality": 65.0,
        }


def exits_for(pattern: dict) -> list[dict]:
    """Measured-move exits: 1.0x, 1.272x, 1.618x the pattern height."""
    h = pattern.get("height", 0)
    base = pattern.get("breakout_level", 0)
    if not h or not base:
        return []
    direction = pattern.get("direction", "long")
    sign = 1 if direction == "long" else -1
    return [
        {"name": "Target 1", "price": base + sign * h * 1.0},
        {"name": "Target 2 (1.272 ext)", "price": base + sign * h * 1.272},
        {"name": "Target 3 (1.618 ext)", "price": base + sign * h * 1.618},
    ]


def detect_all(pair: str, cs: list[dict], cfg: dict) -> list[dict]:
    """Run every detector; return patterns sorted by quality (best first)."""
    out = []
    for fn in (detect_descending, detect_ascending, detect_symmetrical):
        try:
            p = fn(cs, cfg)
        except Exception:  # noqa: BLE001
            p = None
        if p:
            p["pair"] = pair
            p["price"] = cs[-1]["c"]
            p["exits"] = exits_for(p)
            out.append(p)
    for bullish in (True, False):
        try:
            p = detect_flag(cs, bullish)
        except Exception:  # noqa: BLE001
            p = None
        if p:
            p["pair"] = pair
            p["price"] = cs[-1]["c"]
            p["exits"] = exits_for(p)
            out.append(p)
    for bottom in (True, False):
        try:
            p = detect_double(cs, bottom)
        except Exception:  # noqa: BLE001
            p = None
        if p:
            p["pair"] = pair
            p["price"] = cs[-1]["c"]
            p["exits"] = exits_for(p)
            out.append(p)
    # fresh breakouts the app never saw forming (cold start)
    try:
        fb = detect_fresh_breakout(cs, cfg)
    except Exception:  # noqa: BLE001
        fb = None
    if fb and not any(p["type"] == fb["type"] for p in out):
        fb["pair"] = pair
        fb["price"] = cs[-1]["c"]
        fb["exits"] = exits_for(fb)
        out.append(fb)
    # keep the single best triangle + any non-triangle patterns
    tris = [p for p in out if "triangle" in p["type"]]
    others = [p for p in out if "triangle" not in p["type"]]
    best = sorted(tris, key=lambda p: -p["quality"])[:1]
    final = sorted(best + others, key=lambda p: -p["quality"])
    for p in final:
        p.pop("_res_slope", None)
        p.pop("_res_intercept", None)
    return final
