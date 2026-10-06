# ◢ Triangle Monitor

Live Crypto.com pattern-monitoring webapp. **Read-only market monitor — it never
places orders and never touches trading APIs.** All signals are paper-trade
alerts, not financial advice.

## What it does

- **Self-updating universe** — re-polls Crypto.com's instrument list every 10
  minutes. New listings are added automatically, delisted assets are removed,
  and both show up in the "Universe update" banner.
- **California jurisdiction filter** — edit `config/jurisdiction.yaml` and add
  any base symbol that isn't tradeable on a California account to the `blocked`
  list (e.g. `["STX"]`). The public API can't confirm per-state availability,
  so verify in the Crypto.com app.
- **Pattern labels** — every liquid USDT pair (≥ $50k/day quote volume) is
  scanned on hourly candles and labeled:
  - Descending / Ascending / Symmetrical Triangle Pattern
  - Bull / Bear Flag and Double Top / Double Bottom (complementary patterns)
  - ⚡ Breakouts (confirmed)
  - Trend Extensions (measured-move exits T1 / 1.272 / 1.618)
- **Active Pattern Markup** — open any coin to see the macro chart with the
  structural lines plotted:
  - *Upper Resistance Line* (red) — descending trendline through sequential
    lower highs = distribution pressure from sellers
  - *Lower Base Line* (green) — flat structural floor = accumulation holding
  - *Apex Compression* marker — price coiling in the final third of the triangle
  - Breakout level, invalidation level, and all three exits drawn as price lines
- **FLASH alerts** — the moment a breakout is confirmed by an hourly close, the
  screen flashes green with the coin name (plus an audio ping). If price breaks
  the invalidation level before the exits are reached, the screen flashes red:
  **ABORT ABORT** + the coin.
- **Live feed** — every identification, breakout, target hit, and abort streams
  to the dashboard in real time (Server-Sent Events).

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

## Deploy on Render (free)

1. Create a GitHub repo (e.g. `triangle-monitor`) and upload this project's files.
2. In Render: **New → Web Service → connect the repo**. `render.yaml` is
   detected automatically (Python, free plan, health check on `/api/health`).
3. Deploy. The first full scan takes ~1–2 minutes after boot.

**Free-tier note:** Render's free plan sleeps the service when nobody's visiting,
so monitoring pauses while asleep and resumes on the next visit. If you want
true 24/7 watching, Render's Starter plan (~$7/mo) keeps it always on.

## Tuning

- `config/settings.yaml` — scan intervals, liquidity floor, pattern strictness
  (support band width, trendline R², coil ratio, breakout confirmation).
- `config/jurisdiction.yaml` — California blocklist.

## How the states work

`IDENTIFIED` (forming) → `BREAKOUT CONFIRMED` (flash) → exits tracked one by
one → `ABORT` (flash) if the invalidation level breaks first, or `COMPLETED`
when all three exits hit. A pattern that quietly falls apart dissolves with no
alert. State persists in `data/lifecycle.json` across restarts.
