"""
Core scanning logic, decoupled from Telegram/Bitquery specifics where possible.
"""
import asyncio
import logging
from datetime import datetime, timezone

from bitquery_client import BitqueryClient
from state_store import StateStore
from telegram_notifier import TelegramNotifier

log = logging.getLogger("scanner")

EXPLORER_URLS = {
    "solana": "https://solscan.io/token/{address}",
    "ethereum": "https://etherscan.io/token/{address}",
    "base": "https://basescan.org/token/{address}",
    "bsc": "https://bscscan.com/token/{address}",
    "arbitrum": "https://arbiscan.io/token/{address}",
    "optimism": "https://optimistic.etherscan.io/token/{address}",
    "polygon": "https://polygonscan.com/token/{address}",
    "robinhood": "https://robinhoodchain.blockscout.com/token/{address}",
    "arc": "https://explorer.arc.io/token/{address}",
}


def explorer_url(chain: str, address: str) -> str:
    template = EXPLORER_URLS.get(chain, "https://example.com/{address}")
    return template.format(address=address)


class Scanner:
    def __init__(
        self,
        bitquery: BitqueryClient,
        notifier: TelegramNotifier,
        state: StateStore,
        chains: list[str],
        num_buys_to_check: int,
        usd_threshold: float,
        poll_interval_seconds: int,
        watch_timeout_seconds: int,
        protocol_filters: dict | None = None,
        max_checks_per_tick: int = 5,
        max_token_age_hours: float = 6.0,
    ):
        self.bitquery = bitquery
        self.notifier = notifier
        self.state = state
        self.chains = chains
        self.protocol_filters = protocol_filters or {}
        self.max_checks_per_tick = max_checks_per_tick
        self.max_token_age_hours = max_token_age_hours
        self.num_buys_to_check = num_buys_to_check
        self.usd_threshold = usd_threshold
        self.poll_interval_seconds = poll_interval_seconds
        self.watch_timeout_seconds = watch_timeout_seconds
        self._stop = asyncio.Event()

    def stop(self):
        self._stop.set()

    async def run_forever(self):
        log.info(
            "Scanner starting. chains=%s threshold=$%s buys=%s",
            self.chains, self.usd_threshold, self.num_buys_to_check,
        )
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("Error during scan tick")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _tick(self):
        for chain in self.chains:
            await self._discover_new_tokens(chain)
        await self._check_watched_tokens()
        self._expire_stale_watches()

    async def _discover_new_tokens(self, chain: str):
        try:
            candidates = await self.bitquery.new_launches(
                chain, protocol_family=self.protocol_filters.get(chain)
            )
        except Exception:
            log.exception("Failed to fetch new launches for %s", chain)
            return

        for c in candidates:
            address = c["address"]
            if self.state.is_alerted(chain, address):
                continue
            self.state.get_or_create_watch(chain, address, c["symbol"], c["name"])
        self.state.save()

    async def _check_watched_tokens(self):
        # Oldest-watched first, capped per tick - with the Bitquery client now
        # throttling itself to one request every few seconds, checking every
        # watched token every tick would just queue up behind that throttle
        # and the tick would never finish. Spreading the population across
        # several ticks keeps each tick fast and keeps total requests/minute
        # bounded regardless of how many tokens are being watched at once.
        items = sorted(
            self.state.watching.items(), key=lambda kv: kv[1].first_seen_at
        )[: self.max_checks_per_tick]

        for key, watched in items:
            try:
                buys = await self.bitquery.first_buys(
                    watched.chain, watched.address, self.num_buys_to_check
                )
            except Exception:
                log.exception(
                    "Failed to fetch buys for %s/%s", watched.chain, watched.address
                )
                continue

            watched.buys = [{"usd": b["usd"], "time": b.get("time") or ""} for b in buys]
            self.state.save()

            if len(watched.buys) >= self.num_buys_to_check:
                total = watched.total_usd()
                is_fresh = self._is_genuinely_new(watched)
                if total >= self.usd_threshold and is_fresh:
                    await self._fire_alert(watched, total)
                elif total >= self.usd_threshold and not is_fresh:
                    log.info(
                        "Skipping alert for %s/%s: cleared $ threshold but first "
                        "trade (%s) is older than %.1fh - not a new launch",
                        watched.chain, watched.address, watched.buys[0]["time"],
                        self.max_token_age_hours,
                    )
                # Whether it passed the bar or not, we've now evaluated the
                # first N buys — stop watching either way so we don't keep
                # re-querying it forever.
                self.state.mark_alerted(watched.chain, watched.address) if total >= self.usd_threshold \
                    else self.state.drop_watch(watched.chain, watched.address)
                self.state.save()

    def _is_genuinely_new(self, watched) -> bool:
        """The state store only knows a token is 'new to us' - that resets on
        every redeploy, so on its own it's not reliable. This checks the
        token's actual first-ever trade time (from the ascending-order buys
        query, so buys[0] IS the earliest trade Bitquery has for it) against
        a real age cutoff, so an established token isn't mistaken for a
        fresh launch just because the bot forgot it existed."""
        if not watched.buys:
            return False
        first_trade_time = watched.buys[0].get("time")
        if not first_trade_time:
            # No timestamp came back - fail safe by not alerting rather than
            # risk a false positive on an old token.
            return False
        try:
            ts = datetime.fromisoformat(first_trade_time.replace("Z", "+00:00"))
        except ValueError:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        return age_hours <= self.max_token_age_hours

    async def _fire_alert(self, watched, total_usd: float):
        log.info(
            "ALERT %s %s (%s) first %d buys = $%.0f",
            watched.chain, watched.symbol, watched.address,
            self.num_buys_to_check, total_usd,
        )
        await self.notifier.send_launch_alert(
            chain=watched.chain,
            symbol=watched.symbol,
            name=watched.name,
            address=watched.address,
            total_usd=total_usd,
            num_buys=self.num_buys_to_check,
            explorer_url=explorer_url(watched.chain, watched.address),
        )

    def _expire_stale_watches(self):
        for w in self.state.expired_watches(self.watch_timeout_seconds):
            log.info("Timing out watch on %s/%s (never reached %d buys)",
                      w.chain, w.address, self.num_buys_to_check)
            self.state.drop_watch(w.chain, w.address)
        self.state.save()
