"""
Graduation scouts. These run alongside the "first N buys" scanner and alert on
bonding-curve milestones instead of raw buy volume:

  - Solana / pump.fun: tokens about to graduate (curve progress >= threshold)
  - Solana / pump.fun: tokens that just graduated to PumpSwap
  - Robinhood / Pons:  tokens that just graduated to their Uniswap v4 pool

Plus a freshness check for Arc: a token only counts as new if one of Arc's
known launchpads emitted a launch event for it recently.

Every query here is copied from (or minimally adapted from) Bitquery's docs:
  - docs.bitquery.io/docs/blockchain/Solana/Pumpfun/Pump-Fun-Marketcap-Bonding-Curve-API/
  - docs.bitquery.io/docs/blockchain/robinhood/pons-api/
  - docs.bitquery.io/docs/blockchain/arc-mainnet/arc-mainnet-launchpads-api/
They were not run against a live key while writing this file, so check the
logs after the first deploy (see README).
"""
import html
import logging
from datetime import datetime, timezone

log = logging.getLogger("scouts")

# ---------------------------------------------------------------- Solana ---

PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

# "Top 100 About to Graduate Pump Fun Tokens" from Bitquery's docs, with the
# calculated field aliased to `progress`.
SOLANA_NEAR_GRAD_QUERY = """
{
  Solana {
    DEXPools(
      limitBy: { by: Pool_Market_BaseCurrency_MintAddress, count: 1 }
      limit: { count: 100 }
      orderBy: { ascending: Pool_Base_PostAmount }
      where: {
        Pool: {
          Base: { PostAmount: { gt: "206900000" } }
          Dex: { ProgramAddress: { is: "%s" } }
          Market: {
            QuoteCurrency: {
              MintAddress: {
                in: [
                  "11111111111111111111111111111111"
                  "So11111111111111111111111111111111111111112"
                ]
              }
            }
          }
        }
        Transaction: { Result: { Success: true } }
        Block: { Time: { since_relative: { minutes_ago: 5 } } }
      }
    ) {
      progress: calculate(
        expression: "100 - ((($Pool_Base_Balance - 206900000) * 100) / 793100000)"
      )
      Pool {
        Market { BaseCurrency { MintAddress Name Symbol } }
        Base { Balance: PostAmount(maximum: Block_Time) }
        Quote { PostAmountInUSD }
      }
    }
  }
}
""" % PUMPFUN_PROGRAM

# "Latest Pump Fun Token Migrations to PumpSwap" from Bitquery's docs,
# trimmed to the fields we read.
SOLANA_GRADUATED_QUERY = """
query {
  Solana {
    Instructions(
      limit: { count: 20 }
      orderBy: { descending: Block_Slot }
      where: {
        Transaction: { Result: { Success: true } }
        Instruction: {
          Depth: { eq: 1 }
          Program: {
            Method: { is: "create_pool" }
            Address: { is: "%s" }
            Arguments: {
              includes: [{ Value: { Address: { notIn: ["11111111111111111111111111111111"] } } }]
            }
          }
        }
      }
    ) {
      Block { Time }
      Instruction {
        Program { AccountNames }
        Accounts { Address Token { Mint } }
      }
      Transaction { Signature }
    }
  }
}
""" % PUMPSWAP_PROGRAM

# ------------------------------------------------------- Robinhood / Pons ---

PONS_FACTORY = "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"

# Decoded PoolGraduated events on the Pons V2 factory, last 30 minutes.
PONS_GRADUATED_QUERY = """
{
  EVM(network: robinhood) {
    Events(
      limit: { count: 50 }
      orderBy: { descending: Block_Time }
      where: {
        LogHeader: { Address: { is: "%s" } }
        Log: { Signature: { Name: { is: "PoolGraduated" } } }
        Block: { Time: { since_relative: { minutes_ago: 30 } } }
      }
    ) {
      Block { Time }
      Transaction { Hash }
      Arguments {
        Name
        Value {
          ... on EVM_ABI_Address_Value_Arg { address }
          ... on EVM_ABI_BigInt_Value_Arg { bigInteger }
        }
      }
    }
  }
}
""" % PONS_FACTORY

# ------------------------------------------------------------------- Arc ---

# Launch contract -> launch event signature hash, from Bitquery's Arc
# launchpads page. The launched token is in Topics[1] for all of these.
ARC_LAUNCHPADS = {
    "0xb021be536808f551b31789422fd28a6c9c6e97da": "1d8917231579f8ce39407f0d616f36f357b07329b0ce5164d0754ac15145ce0a",  # Argus
    "0x4b638c1502a07a8e1a26112ee98f51a3f34bc93a": "851d681a32f0efba577c4a1bd412f74b575764a6b91e499a05a48a23f3821d66",  # RadarDEX Classic
    "0x2d933ce4bde6f3d99540b5d7886b383e59b2b2f8": "851d681a32f0efba577c4a1bd412f74b575764a6b91e499a05a48a23f3821d66",  # RadarDEX Reflection
    "0xcad7ee36ac193bf2eddb7b3e2736c5bdb8269c8b": "875522b092d9e19a1de359e4bd218090d582fa521c9733889acf1a5ff1941255",  # Tolly
    "0x0dcad158e98bc24455f9e94f46709d8a5f6d1255": "0b4cfda446fdf9ec5a85855f088c154869eb62e3e723d7d80319b680f90e0cfd",  # Warp
    "0x297cebc4de347347205cd08667b56ee951dd8810": "8e83c293b82cf6e864a90c1ccffea5e0f1ec23b271eff78e78f1dbd5e32a9c7d",  # Archemist V2
}

ARC_LAUNCH_CHECK_QUERY = """
query ArcLaunchCheck($topic: String!, $hours: Int!) {
  EVM(network: arc) {
    Events(
      limit: { count: 1 }
      where: {
        LogHeader: { Address: { in: [%s] } }
        Log: { Signature: { SignatureHash: { in: [%s] } } }
        Topics: { includes: [{ Hash: { is: $topic } }] }
        Block: { Time: { since_relative: { hours_ago: $hours } } }
      }
    ) {
      Block { Time }
      LogHeader { Address }
    }
  }
}
""" % (
    " ".join(f'"{a}"' for a in ARC_LAUNCHPADS),
    " ".join(f'"{h}"' for h in sorted(set(ARC_LAUNCHPADS.values()))),
)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _minutes_ago(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds() / 60


def extract_pumpswap_mint(row: dict) -> str | None:
    """Find the graduated token's mint in a PumpSwap create_pool row.
    Accounts line up with AccountNames; the token is the `base_mint` account.
    Fallback: pump.fun mints end in 'pump'."""
    ins = row.get("Instruction") or {}
    names = (ins.get("Program") or {}).get("AccountNames") or []
    accounts = ins.get("Accounts") or []
    for name, acc in zip(names, accounts):
        if name == "base_mint" and acc.get("Address"):
            return acc["Address"]
    for acc in accounts:
        addr = acc.get("Address") or ""
        if addr.endswith("pump"):
            return addr
    return None


def extract_argument(row: dict, name: str) -> str | None:
    for arg in row.get("Arguments") or []:
        if arg.get("Name") == name:
            value = arg.get("Value") or {}
            return value.get("address") or value.get("bigInteger")
    return None


class Scouts:
    def __init__(
        self,
        bitquery,
        notifier,
        state,
        chains: list[str],
        near_grad_pct: float = 90.0,
        grad_max_age_minutes: float = 15.0,
        excluded=None,
    ):
        self.bitquery = bitquery
        self.notifier = notifier
        self.state = state
        self.chains = set(chains)
        self.near_grad_pct = near_grad_pct
        self.grad_max_age_minutes = grad_max_age_minutes
        # Optional callable(symbol, name) -> bool, shared with the scanner.
        self.excluded = excluded or (lambda symbol, name: False)
        self.names: dict[str, tuple[str, str]] = {}

    async def run_once(self):
        jobs = []
        if "solana" in self.chains:
            jobs += [self._solana_near_graduation, self._solana_graduated]
        if "robinhood" in self.chains:
            jobs.append(self._pons_graduated)
        for job in jobs:
            try:
                await job()
            except Exception:
                log.exception("Scout %s failed", job.__name__)

    # ------------------------------------------------------------ Solana

    async def _solana_near_graduation(self):
        data = await self.bitquery.query(SOLANA_NEAR_GRAD_QUERY)
        for row in data["Solana"]["DEXPools"]:
            try:
                progress = float(row.get("progress") or 0)
            except (TypeError, ValueError):
                continue
            cur = row["Pool"]["Market"]["BaseCurrency"]
            mint = cur.get("MintAddress")
            symbol, name = cur.get("Symbol") or "?", cur.get("Name") or "?"
            if not mint:
                continue
            self.names[mint] = (symbol, name)
            if progress < self.near_grad_pct or progress > 100:
                continue
            if self.excluded(symbol, name):
                continue
            if self.state.is_alerted("neargrad-solana", mint):
                continue
            sol_usd = (row["Pool"].get("Quote") or {}).get("PostAmountInUSD")
            lines = [f"Bonding curve: <b>{progress:.1f}%</b>"]
            if sol_usd:
                lines.append(f"Curve liquidity: <b>${float(sol_usd):,.0f}</b>")
            await self._send(
                "🔥 About to graduate", "solana", mint, symbol, name, lines,
                links=[("pump.fun", f"https://pump.fun/coin/{mint}"),
                       ("DexScreener", f"https://dexscreener.com/solana/{mint}")],
            )
            self.state.mark_alerted("neargrad-solana", mint)

    async def _solana_graduated(self):
        data = await self.bitquery.query(SOLANA_GRADUATED_QUERY)
        for row in data["Solana"]["Instructions"]:
            mint = extract_pumpswap_mint(row)
            if not mint or self.state.is_alerted("grad-solana", mint):
                continue
            age = _minutes_ago(_parse_time((row.get("Block") or {}).get("Time")))
            if age is None or age > self.grad_max_age_minutes:
                # Old migration (e.g. right after a restart) - remember it so
                # we don't keep re-checking it, but don't alert.
                self.state.mark_alerted("grad-solana", mint)
                continue
            symbol, name = await self._name_for("solana", mint)
            if self.excluded(symbol, name):
                self.state.mark_alerted("grad-solana", mint)
                continue
            await self._send(
                "🎓 Just graduated to PumpSwap", "solana", mint, symbol, name,
                [f"Graduated <b>{age:.0f} min</b> ago"],
                links=[("pump.fun", f"https://pump.fun/coin/{mint}"),
                       ("DexScreener", f"https://dexscreener.com/solana/{mint}")],
            )
            self.state.mark_alerted("grad-solana", mint)

    # ---------------------------------------------------------- Robinhood

    async def _pons_graduated(self):
        data = await self.bitquery.query(PONS_GRADUATED_QUERY)
        for row in data["EVM"]["Events"]:
            token = extract_argument(row, "token")
            if not token or self.state.is_alerted("grad-robinhood", token):
                continue
            age = _minutes_ago(_parse_time((row.get("Block") or {}).get("Time")))
            if age is None or age > self.grad_max_age_minutes:
                self.state.mark_alerted("grad-robinhood", token)
                continue
            symbol, name = await self._name_for("robinhood", token)
            if self.excluded(symbol, name):
                self.state.mark_alerted("grad-robinhood", token)
                continue
            await self._send(
                "🎓 Just graduated on Pons", "robinhood", token, symbol, name,
                [f"Graduated <b>{age:.0f} min</b> ago"],
                links=[("Explorer", f"https://robinhoodchain.blockscout.com/token/{token}")],
            )
            self.state.mark_alerted("grad-robinhood", token)

    # ---------------------------------------------------------------- Arc

    async def arc_recently_launched(self, token: str, hours: int) -> bool:
        """True if an Arc launchpad emitted a launch event for `token`
        within the last `hours` hours."""
        topic = "0x" + "0" * 24 + token.lower().replace("0x", "")
        data = await self.bitquery.query(
            ARC_LAUNCH_CHECK_QUERY, {"topic": topic, "hours": int(max(1, hours))}
        )
        return len(data["EVM"]["Events"]) > 0

    # ------------------------------------------------------------ helpers

    async def _name_for(self, chain: str, address: str) -> tuple[str, str]:
        if address in self.names:
            return self.names[address]
        try:
            info = await self.bitquery.token_info(chain, address)
        except Exception:
            log.exception("Name lookup failed for %s/%s", chain, address)
            info = {}
        result = (info.get("symbol") or "?", info.get("name") or "?")
        self.names[address] = result
        return result

    async def _send(self, title, chain, address, symbol, name, lines, links):
        log.info("SCOUT %s %s %s (%s)", title, chain, symbol, address)
        body = [
            f"<b>{html.escape(title)}</b>",
            "",
            f"<b>{html.escape(symbol)}</b> ({html.escape(name)})",
            f"Chain: <code>{chain}</code>",
            *lines,
            f"Contract: <code>{html.escape(address)}</code>",
            " | ".join(f'<a href="{u}">{html.escape(t)}</a>' for t, u in links),
        ]
        await self.notifier.send_html("\n".join(body))
