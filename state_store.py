"""
Very small JSON-file-backed state store.

Tracks:
  - tokens we've already alerted on (so we never double-alert)
  - tokens we're currently watching, and the buys collected so far for each

This is intentionally simple (no DB dependency). Swap for SQLite/Redis if you
outgrow it.
"""
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


@dataclass
class WatchedToken:
    chain: str
    address: str
    symbol: str
    name: str
    first_seen_at: float
    buys: list = field(default_factory=list)  # list of {"usd": float, "time": iso str}

    def total_usd(self) -> float:
        return sum(b["usd"] for b in self.buys)


class StateStore:
    def __init__(self, path: str = STATE_FILE):
        self.path = path
        self.alerted: set[str] = set()
        self.watching: dict[str, WatchedToken] = {}
        self._load()

    def _key(self, chain: str, address: str) -> str:
        return f"{chain}:{address.lower()}"

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            self.alerted = set(data.get("alerted", []))
            self.watching = {
                k: WatchedToken(**v) for k, v in data.get("watching", {}).items()
            }
        except (json.JSONDecodeError, TypeError, KeyError):
            # Corrupt or old-format state file — start fresh rather than crash.
            self.alerted = set()
            self.watching = {}

    def save(self):
        data = {
            "alerted": list(self.alerted),
            "watching": {k: asdict(v) for k, v in self.watching.items()},
        }
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.path)

    def is_alerted(self, chain: str, address: str) -> bool:
        return self._key(chain, address) in self.alerted

    def mark_alerted(self, chain: str, address: str):
        self.alerted.add(self._key(chain, address))
        self.watching.pop(self._key(chain, address), None)
        self.save()

    def get_or_create_watch(
        self, chain: str, address: str, symbol: str, name: str
    ) -> WatchedToken:
        key = self._key(chain, address)
        if key not in self.watching:
            self.watching[key] = WatchedToken(
                chain=chain,
                address=address,
                symbol=symbol,
                name=name,
                first_seen_at=time.time(),
            )
        return self.watching[key]

    def drop_watch(self, chain: str, address: str):
        self.watching.pop(self._key(chain, address), None)

    def expired_watches(self, timeout_seconds: int) -> list[WatchedToken]:
        now = time.time()
        return [
            w for w in self.watching.values()
            if now - w.first_seen_at > timeout_seconds
        ]
