"""Universe management: poll Crypto.com instruments, diff adds/removes,
apply the California jurisdiction blocklist and the liquidity floor."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

INSTRUMENTS_URL = "https://api.crypto.com/exchange/v1/public/get-instruments"
TICKERS_URL = "https://api.crypto.com/exchange/v1/public/get-tickers"

# fiat currencies occasionally listed as *_USDT pairs — not crypto assets
FIAT = {"EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "BRL", "MXN", "ARS",
        "NZD", "SGD", "HKD", "TRY", "ZAR", "AED", "SEK", "NOK", "DKK"}


def _curl_json(url: str, timeout: int = 30) -> dict:
    out = subprocess.run(
        ["curl", "-s", "-m", str(timeout), url],
        capture_output=True, text=True, timeout=timeout + 10,
    )
    data = json.loads(out.stdout)
    if data.get("code") != 0:
        raise RuntimeError(f"API error: {data}")
    return data["result"]


def load_blocked() -> set[str]:
    try:
        cfg = yaml.safe_load((CONFIG_DIR / "jurisdiction.yaml").read_text()) or {}
    except FileNotFoundError:
        cfg = {}
    return {str(s).upper() for s in (cfg.get("blocked") or [])}


def load_settings() -> dict:
    try:
        return yaml.safe_load((CONFIG_DIR / "settings.yaml").read_text()) or {}
    except FileNotFoundError:
        return {}


class Universe:
    """Maintains the live tradeable pair list."""

    def __init__(self) -> None:
        self.settings = load_settings()
        u = self.settings.get("universe", {})
        self.quote = u.get("quote", "USDT")
        self.min_qv = float(u.get("min_quote_volume_24h", 50000))
        self.pairs: dict[str, dict] = {}   # "BTC_USDT" -> {base, price, qv_24h}
        self.added: list[dict] = []        # recent additions {pair, ts}
        self.removed: list[dict] = []      # recent removals {pair, ts}
        self.last_refresh: float = 0.0
        self.last_error: str | None = None

    def refresh(self) -> dict:
        """Re-poll instruments + tickers, diff the universe, apply filters."""
        t0 = time.time()
        try:
            instruments = _curl_json(INSTRUMENTS_URL)["data"]
            tickers = _curl_json(TICKERS_URL)["data"]
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            return {"ok": False, "error": str(e)}

        blocked = load_blocked()
        # ticker lookup: instrument_name -> (last_price, 24h base volume)
        tick = {}
        for t in tickers:
            try:
                tick[t["i"]] = (float(t["a"]), float(t["v"]))
            except (KeyError, ValueError, TypeError):
                continue

        new_pairs: dict[str, dict] = {}
        for inst in instruments:
            try:
                if inst.get("inst_type") != "CCY_PAIR":
                    continue
                if not inst.get("tradable", True):
                    continue
                base = inst["base_ccy"]
                quote = inst["quote_ccy"]
                if quote != self.quote:
                    continue
                if base.upper() in blocked or base.upper() in FIAT:
                    continue
                name = f"{base}_{quote}"
                price, vol24 = tick.get(name, (0.0, 0.0))
                qv = price * vol24
                if qv < self.min_qv:
                    continue
                new_pairs[name] = {"base": base, "price": price, "qv_24h": qv}
            except (KeyError, ValueError, TypeError):
                continue

        old = set(self.pairs)
        new = set(new_pairs)
        now = time.time()
        for p in sorted(new - old):
            self.added.append({"pair": p, "ts": now})
        for p in sorted(old - new):
            self.removed.append({"pair": p, "ts": now})
        # keep event history bounded
        self.added = self.added[-50:]
        self.removed = self.removed[-50:]

        self.pairs = new_pairs
        self.last_refresh = now
        self.last_error = None
        return {
            "ok": True,
            "pairs": len(new_pairs),
            "added": sorted(new - old),
            "removed": sorted(old - new),
            "took_s": round(time.time() - t0, 1),
        }

    def snapshot(self) -> dict:
        return {
            "quote": self.quote,
            "pairs": len(self.pairs),
            "last_refresh": self.last_refresh,
            "last_error": self.last_error,
            "min_qv": self.min_qv,
            "recently_added": self.added[-10:][::-1],
            "recently_removed": self.removed[-10:][::-1],
        }
