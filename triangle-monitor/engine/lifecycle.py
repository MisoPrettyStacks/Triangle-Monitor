"""Pattern lifecycle: IDENTIFIED -> BREAKOUT_CONFIRMED -> exits -> ABORT.

Every state change produces an event for the live feed. Breakout and ABORT
events drive the flashing banners in the UI.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
STATE_FILE = DATA_DIR / "lifecycle.json"

FLASH_RETAIN_S = 30 * 60


class Lifecycle:
    def __init__(self) -> None:
        self.patterns: dict[str, dict] = {}  # key "PAIR:type" -> record
        self.events: list[dict] = []          # newest last, capped
        self._load()

    # ------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            data = json.loads(STATE_FILE.read_text())
            self.patterns = data.get("patterns", {})
            self.events = data.get("events", [])[-200:]
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        STATE_FILE.write_text(json.dumps(
            {"patterns": self.patterns, "events": self.events[-200:]},
            default=str))

    # ------------------------------------------------ events
    def _emit(self, kind: str, pair: str, pattern: dict, extra: dict | None = None):
        ev = {
            "kind": kind,          # identified | breakout_confirmed | abort |
                                   # target_hit | completed | dissolved
            "pair": pair,
            "pattern_type": pattern.get("type"),
            "label": pattern.get("label"),
            "price": pattern.get("price"),
            "ts": time.time(),
        }
        if extra:
            ev.update(extra)
        self.events.append(ev)
        self.events = self.events[-200:]
        return ev

    # ------------------------------------------------ main update
    def update(self, pair: str, detected: list[dict],
               last_close: float) -> list[dict]:
        """Reconcile detected patterns with active records. Returns new events."""
        new_events: list[dict] = []
        seen_keys = set()

        for p in detected:
            key = f"{pair}:{p['type']}"
            seen_keys.add(key)
            rec = self.patterns.get(key)

            if rec is None:
                # brand-new identification
                status = "forming"
                rec = {
                    "pair": pair, "type": p["type"], "label": p["label"],
                    "direction": p["direction"],
                    "status": status,
                    "first_seen": time.time(),
                    "last_seen": time.time(),
                    "pattern": p,
                    "targets_hit": [],
                }
                self.patterns[key] = rec
                if p.get("already_broken"):
                    # cold start: breakout already happened — flash it now
                    rec["status"] = "breakout"
                    rec["breakout_price"] = last_close
                    rec["breakout_ts"] = time.time()
                    new_events.append(self._emit(
                        "breakout_confirmed", pair, p,
                        {"breakout_level": p["breakout_level"],
                         "flash_until": time.time() + FLASH_RETAIN_S,
                         "note": "breakout already in progress at first scan"}))
                else:
                    new_events.append(self._emit("identified", pair, p))
                continue

            rec["last_seen"] = time.time()
            rec["pattern"] = p  # refresh geometry

            if rec["status"] == "forming":
                # check for confirmed breakout on close beyond the level
                lvl = p["breakout_level"]
                if p["direction"] == "long" and last_close > lvl:
                    rec["status"] = "breakout"
                    rec["breakout_price"] = last_close
                    rec["breakout_ts"] = time.time()
                    new_events.append(self._emit(
                        "breakout_confirmed", pair, p,
                        {"breakout_level": lvl, "flash_until": time.time() + FLASH_RETAIN_S}))
                elif p["direction"] == "short" and last_close < lvl:
                    rec["status"] = "breakout"
                    rec["breakout_price"] = last_close
                    rec["breakout_ts"] = time.time()
                    new_events.append(self._emit(
                        "breakout_confirmed", pair, p,
                        {"breakout_level": lvl, "flash_until": time.time() + FLASH_RETAIN_S}))

            elif rec["status"] == "breakout":
                inv = p["invalidation_level"]
                aborted = (p["direction"] == "long" and last_close < inv) or \
                          (p["direction"] == "short" and last_close > inv)
                if aborted and len(rec["targets_hit"]) < len(p.get("exits", [])):
                    rec["status"] = "aborted"
                    new_events.append(self._emit(
                        "abort", pair, p,
                        {"invalidation_level": inv,
                         "flash_until": time.time() + FLASH_RETAIN_S}))
                else:
                    # check exits
                    for i, ex in enumerate(p.get("exits", [])):
                        if i in rec["targets_hit"]:
                            continue
                        hit = (p["direction"] == "long" and last_close >= ex["price"]) or \
                              (p["direction"] == "short" and last_close <= ex["price"])
                        if hit:
                            rec["targets_hit"].append(i)
                            new_events.append(self._emit(
                                "target_hit", pair, p,
                                {"target": ex["name"], "target_price": ex["price"]}))
                    if len(rec["targets_hit"]) >= len(p.get("exits", [])) and p.get("exits"):
                        rec["status"] = "completed"
                        new_events.append(self._emit("completed", pair, p))

        # post-breakout tracking: keep evaluating exits/invalidation from the
        # stored geometry even after the pattern stops being "detected"
        for key, rec in list(self.patterns.items()):
            if rec["pair"] != pair or rec["status"] != "breakout":
                continue
            if key in seen_keys:
                continue  # already handled above with fresh geometry
            p = rec["pattern"]
            rec["last_seen"] = time.time()
            inv = p["invalidation_level"]
            aborted = (p["direction"] == "long" and last_close < inv) or \
                      (p["direction"] == "short" and last_close > inv)
            if aborted and len(rec["targets_hit"]) < len(p.get("exits", [])):
                rec["status"] = "aborted"
                new_events.append(self._emit(
                    "abort", pair, p,
                    {"invalidation_level": inv,
                     "flash_until": time.time() + FLASH_RETAIN_S}))
                continue
            for i, ex in enumerate(p.get("exits", [])):
                if i in rec["targets_hit"]:
                    continue
                hit = (p["direction"] == "long" and last_close >= ex["price"]) or \
                      (p["direction"] == "short" and last_close <= ex["price"])
                if hit:
                    rec["targets_hit"].append(i)
                    new_events.append(self._emit(
                        "target_hit", pair, p,
                        {"target": ex["name"], "target_price": ex["price"]}))
            if len(rec["targets_hit"]) >= len(p.get("exits", [])) and p.get("exits"):
                rec["status"] = "completed"
                new_events.append(self._emit("completed", pair, p))

        # patterns that vanished while still forming -> dissolved (quiet)
        for key, rec in list(self.patterns.items()):
            if rec["pair"] == pair and key not in seen_keys \
                    and rec["status"] == "forming":
                if time.time() - rec["last_seen"] > 3 * 3600:
                    rec["status"] = "dissolved"
                    new_events.append(self._emit("dissolved", pair, rec["pattern"]))

        # prune old terminal records
        cutoff = time.time() - 24 * 3600
        self.patterns = {
            k: r for k, r in self.patterns.items()
            if r["status"] in ("forming", "breakout")
            or r.get("last_seen", 0) > cutoff
        }
        self._save()
        return new_events

    # ------------------------------------------------ queries
    def active(self) -> list[dict]:
        recs = [r for r in self.patterns.values()
                if r["status"] in ("forming", "breakout")]
        return sorted(recs, key=lambda r: (r["status"] != "breakout",
                                           -r["pattern"].get("quality", 0)))

    def recent_events(self, n: int = 50) -> list[dict]:
        return self.events[-n:][::-1]

    def flashes(self) -> list[dict]:
        """Currently-active flash banners (breakout / abort)."""
        now = time.time()
        out = []
        for ev in reversed(self.events):
            if ev["kind"] in ("breakout_confirmed", "abort") \
                    and ev.get("flash_until", 0) > now:
                out.append(ev)
        return out
