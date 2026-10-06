"""Candle fetching via curl (python requests has proxy issues in some envs)."""
from __future__ import annotations

import json
import subprocess

CANDLE_URL = (
    "https://api.crypto.com/exchange/v1/public/get-candlestick"
    "?instrument_name={pair}&timeframe={tf}&count={count}"
)


def fetch_candles(pair: str, timeframe: str = "1h", count: int = 168,
                   timeout: int = 30) -> list[dict]:
    """Return sorted list of {t, o, h, l, c, v} (t in ms). Raises on failure."""
    url = CANDLE_URL.format(pair=pair, tf=timeframe, count=count)
    out = subprocess.run(
        ["curl", "-s", "-m", str(timeout), url],
        capture_output=True, text=True, timeout=timeout + 10,
    )
    data = json.loads(out.stdout)
    if data.get("code") != 0:
        raise RuntimeError(f"candle API error for {pair}: {data.get('msg')}")
    candles = []
    for c in data["result"]["data"]:
        try:
            candles.append({
                "t": int(c["t"]),
                "o": float(c["o"]),
                "h": float(c["h"]),
                "l": float(c["l"]),
                "c": float(c["c"]),
                "v": float(c["v"]),
            })
        except (KeyError, ValueError, TypeError):
            continue
    candles.sort(key=lambda x: x["t"])
    # drop a just-forming bar with zero volume at the end
    if candles and candles[-1]["v"] == 0 and len(candles) > 1:
        # keep it only if the previous bar is far older than one timeframe;
        # simpler: drop trailing zero-volume bars (feed lag placeholders)
        while candles and candles[-1]["v"] == 0:
            candles.pop()
    return candles
