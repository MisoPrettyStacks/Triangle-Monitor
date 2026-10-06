"""Triangle Monitor — live Crypto.com pattern monitoring webapp.

Read-only market monitor. It never places orders and never touches trading
APIs. Paper-signal alerts only.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from engine.candles import fetch_candles
from engine.lifecycle import Lifecycle
from engine.patterns import detect_all
from engine.universe import Universe

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def load_settings() -> dict:
    try:
        return yaml.safe_load((BASE_DIR / "config" / "settings.yaml").read_text()) or {}
    except FileNotFoundError:
        return {}


SETTINGS = load_settings()
universe = Universe()
lifecycle = Lifecycle()

# live candle cache: pair -> {"candles": [...], "fetched_at": ts}
candle_cache: dict[str, dict] = {}
cache_lock = threading.Lock()
stop_event = threading.Event()


# ------------------------------------------------------------ poll loop

def poll_universe() -> None:
    res = universe.refresh()
    print(f"[universe] ok={res.get('ok')} pairs={res.get('pairs')} "
          f"added={res.get('added')} removed={res.get('removed')}", flush=True)


def poll_candles() -> None:
    cfg_tf = SETTINGS.get("candles", {}).get("timeframe", "1h")
    cfg_n = int(SETTINGS.get("candles", {}).get("count", 168))
    for pair in list(universe.pairs.keys()):
        if stop_event.is_set():
            break
        try:
            cs = fetch_candles(pair, cfg_tf, cfg_n)
        except Exception as e:  # noqa: BLE001
            print(f"[candles] {pair}: {e}", flush=True)
            continue
        if len(cs) < 60:
            continue
        with cache_lock:
            candle_cache[pair] = {"candles": cs, "fetched_at": time.time()}
        try:
            detected = detect_all(pair, cs, SETTINGS)
        except Exception as e:  # noqa: BLE001
            print(f"[detect] {pair}: {e}", flush=True)
            continue
        events = lifecycle.update(pair, detected, cs[-1]["c"])
        for ev in events:
            print(f"[event] {ev['kind']} {ev['pair']} {ev.get('label')}", flush=True)
        time.sleep(0.4)  # be gentle on the API


def scheduler() -> None:
    u_min = float(SETTINGS.get("universe", {}).get("refresh_minutes", 10))
    c_min = float(SETTINGS.get("candles", {}).get("refresh_minutes", 5))
    poll_universe()
    poll_candles()
    next_u = time.time() + u_min * 60
    next_c = time.time() + c_min * 60
    while not stop_event.wait(15):
        now = time.time()
        if now >= next_u:
            poll_universe()
            next_u = now + u_min * 60
        if now >= next_c:
            poll_candles()
            next_c = now + c_min * 60


# ------------------------------------------------------------ app

app = FastAPI(title="Triangle Monitor")


@app.on_event("startup")
def _startup() -> None:
    t = threading.Thread(target=scheduler, daemon=True, name="poller")
    t.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    stop_event.set()


@app.get("/api/health")
def health():
    return {"ok": True, "ts": time.time()}


@app.get("/api/universe")
def api_universe():
    snap = universe.snapshot()
    with cache_lock:
        cached = list(candle_cache.keys())
    snap["pairs_with_candles"] = len(cached)
    snap["pairs_list"] = sorted(universe.pairs.keys())
    return snap


@app.get("/api/patterns")
def api_patterns():
    return {"patterns": lifecycle.active(), "ts": time.time()}


@app.get("/api/patterns/{pair}")
def api_pattern_detail(pair: str):
    with cache_lock:
        entry = candle_cache.get(pair)
    if not entry:
        return JSONResponse({"error": "no candle data yet"}, status_code=404)
    cs = entry["candles"]
    recs = [r for r in lifecycle.patterns.values() if r["pair"] == pair]
    return {
        "pair": pair,
        "price": cs[-1]["c"],
        "fetched_at": entry["fetched_at"],
        # slim candles for the chart: [t, o, h, l, c]
        "candles": [[c["t"], c["o"], c["h"], c["l"], c["c"]] for c in cs],
        "patterns": recs,
    }


@app.get("/api/events")
def api_events(n: int = 50):
    return {"events": lifecycle.recent_events(n)}


@app.get("/api/flashes")
def api_flashes():
    return {"flashes": lifecycle.flashes()}


@app.get("/api/stream")
async def api_stream(request: Request):
    """Server-sent events: pushes lifecycle events + flashes as they happen."""
    async def gen():
        last_idx = len(lifecycle.events)
        # send current flashes immediately so a fresh client sees them
        yield f"event: flashes\ndata: {json.dumps(lifecycle.flashes())}\n\n"
        while True:
            if await request.is_disconnected():
                break
            evs = lifecycle.events[last_idx:]
            last_idx = len(lifecycle.events)
            for ev in evs:
                yield f"event: lifecycle\ndata: {json.dumps(ev, default=str)}\n\n"
            # heartbeat keeps proxies happy
            yield ": ping\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ------------------------------------------------------------ frontend

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
