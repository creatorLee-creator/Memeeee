"""
Thin client around Bitquery's GraphQL API (https://streaming.bitquery.io/graphql).

Built against Bitquery's own "V2 API cheat sheet" (docs.bitquery.io/docs/start/bitquery-for-ai),
which documents a cross-chain `Trading` cube that covers Solana, Ethereum, Base, BSC, Arbitrum,
Optimism, Polygon, Robinhood Chain and Arc with one identical query shape (just a different
`Network` filter value). That's what this client uses for both "find new tokens" and "get a
token's first N buys" - one code path for every chain instead of one per chain.

Still verify in https://ide.bitquery.io before a real run (see README) - schemas move over time
and this was written without a live key to test against.

Key rules this file follows (from Bitquery's own docs):
  - Use Trading.Trades for cross-chain trade data; filter Pair.Market.Network by the chain's
    *display name* (case-sensitive): "Solana", "Ethereum", "Base", "Binance Smart Chain",
    "Arbitrum", "Optimism", "Matic" (Polygon), "Robinhood", "Arc".
  - EVM token addresses in the Trading cube must be lowercase or you silently get zero rows.
    Solana mint addresses are base58 (mixed case is meaningful) and are NOT lowercased.
  - Use AmountsInUsd.Quote for a trade's USD value, not AmountsInUsd.Base (the docs note .Base
    uses a smoothed reference price that diverges from what was actually paid).
  - pump.fun is a launchpad ON Solana, not a separate chain - it's covered by chain="solana".
    Pass a protocol_family to scope to just one launchpad (e.g. "Pump", "Bags", "Tolly") if you
    only want that launchpad's tokens rather than everything trading on the chain.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp

BITQUERY_URL = "https://streaming.bitquery.io/graphql"

# chain key (used in this project / .env) -> Bitquery's Trading-cube display name for Network
NETWORK_DISPLAY_NAMES = {
    "solana": "Solana",
    "ethereum": "Ethereum",
    "base": "Base",
    "bsc": "Binance Smart Chain",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "polygon": "Matic",
    "robinhood": "Robinhood",   # Robinhood Chain - RWA-focused L2, but also hosts memecoin
                                 # launchpads (Pons, Flap.sh, Bags.fm)
    "arc": "Arc",               # Circle's Arc L1 - hosts the Tolly memecoin launchpad
}

# Recently-traded tokens on a chain (candidates for "just launched").
# We treat "first time our state store has seen this address" as the launch signal -
# see scanner.py. Optionally narrow to one launchpad with protocol_family.
RECENT_TRADES_QUERY = """
query RecentTrades($network: String!, $limit: Int!, $since: DateTime!) {
  Trading {
    Trades(
      where: {
        Pair: { Market: { Network: { is: $network } } }
        Side: { is: "Buy" }
        Block: { Time: { since: $since } }
      }
      orderBy: { descending: Block_Time }
      limit: { count: $limit }
    ) {
      Block { Time }
      Pair {
        Token { Address Symbol Name }
        Market { ProtocolFamily }
      }
    }
  }
}
"""

RECENT_TRADES_BY_PROTOCOL_QUERY = """
query RecentTradesByProtocol($network: String!, $protocolFamily: String!, $limit: Int!, $since: DateTime!) {
  Trading {
    Trades(
      where: {
        Pair: {
          Market: { Network: { is: $network }, ProtocolFamily: { is: $protocolFamily } }
        }
        Side: { is: "Buy" }
        Block: { Time: { since: $since } }
      }
      orderBy: { descending: Block_Time }
      limit: { count: $limit }
    ) {
      Block { Time }
      Pair {
        Token { Address Symbol Name }
      }
    }
  }
}
"""

# Earliest buys of a specific token on a specific chain, oldest first.
TOKEN_FIRST_BUYS_QUERY = """
query TokenFirstBuys($token: String!, $network: String!, $limit: Int!) {
  Trading {
    Trades(
      where: {
        Pair: {
          Token: { Address: { is: $token } }
          Market: { Network: { is: $network } }
        }
        Side: { is: "Buy" }
      }
      orderBy: { ascending: Block_Time }
      limit: { count: $limit }
    ) {
      Block { Time }
      AmountsInUsd { Quote }
    }
  }
}
"""

# Existence check only: does this token have ANY trade before `before`? Used
# to confirm a token is genuinely new rather than trusting "earliest trade we
# got back" - which can look recent for an old, established token if the API
# doesn't hand back its full history on this query shape/plan. limit:{count:1}
# keeps this cheap; we only care whether the result set is empty or not.
TOKEN_HAS_EARLIER_TRADE_QUERY = """
query TokenHasEarlierTrade($token: String!, $network: String!, $before: DateTime!) {
  Trading {
    Trades(
      where: {
        Pair: {
          Token: { Address: { is: $token } }
          Market: { Network: { is: $network } }
        }
        Side: { is: "Buy" }
        Block: { Time: { before: $before } }
      }
      limit: { count: 1 }
    ) {
      Block { Time }
    }
  }
}
"""


def _normalize_address(chain: str, address: str) -> str:
    """EVM (0x...) addresses must be lowercase in the Trading cube. Solana mint
    addresses are base58 and must be left as-is."""
    if address.startswith("0x"):
        return address.lower()
    return address


class BitqueryClient:
    def __init__(
        self,
        api_key: str,
        min_seconds_between_requests: float = 3.0,
        discovery_window_minutes: int = 10,
    ):
        self.api_key = api_key
        self.discovery_window_minutes = discovery_window_minutes
        self._session: aiohttp.ClientSession | None = None
        # Self-throttle: never fire requests faster than this, regardless of how
        # often the scanner asks. Trial/free Bitquery plans reject bursts with
        # "access restricted by rate limit: too many requests per minute" - this
        # spaces requests out so we stay under that instead of erroring on it.
        self._min_seconds_between_requests = min_seconds_between_requests
        self._last_request_at = 0.0
        self._throttle_lock = asyncio.Lock()

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
        )
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    async def _throttle(self):
        async with self._throttle_lock:
            now = asyncio.get_event_loop().time()
            wait = self._last_request_at + self._min_seconds_between_requests - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = asyncio.get_event_loop().time()

    async def _query(self, query: str, variables: dict, retries: int = 4) -> dict:
        assert self._session is not None, "Use BitqueryClient as an async context manager"
        last_error = None
        for attempt in range(retries):
            await self._throttle()
            async with self._session.post(
                BITQUERY_URL, json={"query": query, "variables": variables}
            ) as resp:
                payload = await resp.json()
                if "errors" in payload:
                    messages = " ".join(
                        str(e.get("message", e)) for e in payload["errors"]
                    )
                    lower = messages.lower()
                    retryable = "rate limit" in lower or "deadline exceeded" in lower
                    if retryable and attempt < retries - 1:
                        # Back off and try again rather than surfacing an error
                        # for what's usually a transient, self-inflicted burst.
                        backoff = 5 * (attempt + 1)
                        last_error = messages
                        await asyncio.sleep(backoff)
                        continue
                    raise RuntimeError(f"Bitquery error: {payload['errors']}")
                return payload["data"]
        raise RuntimeError(f"Bitquery error (after {retries} retries): {last_error}")

    async def new_launches(
        self, chain: str, limit: int = 50, protocol_family: str | None = None
    ) -> list[dict]:
        """Returns recently-traded tokens on `chain` as [{address, symbol, name}, ...].

        If `protocol_family` is set (e.g. "Pump" for pump.fun on Solana, "Bags" or
        "Tolly"), only trades on that launchpad are considered.
        """
        network = NETWORK_DISPLAY_NAMES.get(chain)
        if not network:
            raise ValueError(
                f"Unsupported chain '{chain}'. Supported: {', '.join(NETWORK_DISPLAY_NAMES)}"
            )

        # Only look at the last few minutes of trades. Without a time window
        # this query scans the chain's whole trade history ordered by time,
        # which is heavy enough that Bitquery sometimes times out on it.
        since = (
            datetime.now(timezone.utc) - timedelta(minutes=self.discovery_window_minutes)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        if protocol_family:
            data = await self._query(
                RECENT_TRADES_BY_PROTOCOL_QUERY,
                {"network": network, "protocolFamily": protocol_family,
                 "limit": limit, "since": since},
            )
        else:
            data = await self._query(
                RECENT_TRADES_QUERY, {"network": network, "limit": limit, "since": since}
            )

        trades = data["Trading"]["Trades"]
        seen = {}
        for t in trades:
            token = t["Pair"]["Token"]
            addr = token["Address"]
            if addr not in seen:
                seen[addr] = {
                    "address": addr,
                    "symbol": token.get("Symbol") or "?",
                    "name": token.get("Name") or "?",
                }
        return list(seen.values())

    async def first_buys(self, chain: str, address: str, limit: int) -> list[dict]:
        """Returns up to `limit` earliest buys of `address` on `chain`, oldest first,
        as [{"usd": float}, ...]."""
        network = NETWORK_DISPLAY_NAMES.get(chain)
        if not network:
            raise ValueError(
                f"Unsupported chain '{chain}'. Supported: {', '.join(NETWORK_DISPLAY_NAMES)}"
            )
        token = _normalize_address(chain, address)
        data = await self._query(
            TOKEN_FIRST_BUYS_QUERY, {"token": token, "network": network, "limit": limit}
        )
        trades = data["Trading"]["Trades"]
        return [
            {
                "usd": float((t.get("AmountsInUsd") or {}).get("Quote") or 0),
                "time": (t.get("Block") or {}).get("Time"),
            }
            for t in trades
        ]

    async def has_earlier_trade(self, chain: str, address: str, before_iso: str) -> bool:
        """True if `address` on `chain` has any buy trade before `before_iso`
        (an ISO 8601 timestamp). This is the authoritative "is this actually a
        new token" check - it doesn't depend on the API handing back a
        token's true full history via first_buys, only on whether anything
        exists strictly before the cutoff."""
        network = NETWORK_DISPLAY_NAMES.get(chain)
        if not network:
            raise ValueError(
                f"Unsupported chain '{chain}'. Supported: {', '.join(NETWORK_DISPLAY_NAMES)}"
            )
        token = _normalize_address(chain, address)
        data = await self._query(
            TOKEN_HAS_EARLIER_TRADE_QUERY,
            {"token": token, "network": network, "before": before_iso},
        )
        return len(data["Trading"]["Trades"]) > 0
