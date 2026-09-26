# Memecoin Launch Scanner (Telegram Bot)

Watches new token launches across **Solana (incl. pump.fun), Ethereum, Base,
BSC, Arbitrum, Optimism, Polygon, Robinhood Chain, and Arc**, and alerts your
Telegram chat when a token's **first N buys** (default 20) add up to
**$5,000+** in total.

It uses Bitquery's cross-chain `Trading` cube, which indexes all of these
chains under one identical query shape — so adding/removing a chain is just
editing `CHAINS` in `.env`, not writing new code. `robinhood` = Robinhood
Chain (hosts the Pons/Flap.sh/Bags.fm memecoin launchpads); `arc` = Circle's
Arc L1 (hosts the Tolly launchpad); pump.fun is a launchpad *on* Solana, not
a separate chain, so it's covered by `solana` (optionally scope to just it —
see `PROTOCOL_FILTERS` below).

## Graduation scouts

Besides the "first N buys" alerts, `scouts.py` sends three extra alert types:

- **🔥 About to graduate (Solana / pump.fun)**: bonding curve at or above
  `NEAR_GRAD_PCT` (default 90%).
- **🎓 Just graduated to PumpSwap (Solana / pump.fun)**.
- **🎓 Just graduated on Pons (Robinhood Chain)**.

On **Arc**, no launchpad graduation data is documented yet, so Arc keeps the
"first N buys" alerts, but a token only counts as new if an Arc launchpad
(Argus, RadarDEX, Tolly, Warp, Archemist) emitted a launch event for it within
`MAX_TOKEN_AGE_HOURS`.

The queries come from Bitquery's docs but were not run live while being
written. After deploying, search the Railway logs for `Scout` - a line like
`Scout _solana_graduated failed` followed by a Bitquery error names the field
to fix. One failing scout does not stop the others.

## How it works

1. Every `POLL_INTERVAL_SECONDS`, it asks Bitquery for recently-traded new
   tokens on each configured chain and starts "watching" any it hasn't seen.
2. For each watched token, it re-checks the token's earliest trades until it
   has seen `NUM_BUYS_TO_CHECK` buys (or the watch times out).
3. Once it has that many buys, it sums their USD value. If the sum is over
   `USD_THRESHOLD`, it sends a Telegram alert with the token, chain, contract
   address, and total. Either way, it stops watching that token (so it's a
   one-shot judgement per token, not continuous re-alerting).
4. State (what's been alerted, what's being watched) is persisted to
   `state.json` so a restart doesn't cause duplicate alerts.

## Deploying from an iPhone (Railway — no computer needed)

This bot has to run on an always-on server somewhere; your phone is just
where the Telegram alerts show up. [Railway](https://railway.app) is the
easiest way to do that entirely from Safari on an iPhone, no terminal
required.

1. **Put the code on GitHub** (also doable from Safari):
   - Go to [github.com](https://github.com), sign in (or create a free
     account), tap **+ → New repository**, name it e.g. `memecoin-bot`,
     make it private, create it.
   - On the new repo's page, tap **Add file → Upload files**, then upload
     every file from this project (unzip it first — the Files app on iOS
     can unzip by tapping the `.zip`). Commit.
2. **Create a Railway project**:
   - Go to [railway.app](https://railway.app), sign up/log in with GitHub.
   - **New Project → Deploy from GitHub repo** → pick `memecoin-bot`.
   - Railway will detect Python via `requirements.txt` and the `Procfile`
     and start deploying automatically — it runs `main.py` as a worker
     process (not a web server, since this bot doesn't serve web traffic).
3. **Set your secrets**: in the Railway project, go to the service's
   **Variables** tab and add each value from `.env.example`:
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BITQUERY_API_KEY`, and
   optionally `CHAINS`, `NUM_BUYS_TO_CHECK`, `USD_THRESHOLD`,
   `POLL_INTERVAL_SECONDS`, `WATCH_TIMEOUT_SECONDS` (defaults are fine to
   start). Railway restarts the service automatically when you save.
4. **Watch the logs**: the **Deployments** tab shows live logs so you can
   confirm it says "Scanner starting..." with no errors.
5. Message your bot on Telegram — you should get the `/start` welcome, and
   from then on it'll DM you whenever a token clears the threshold.

Railway's free trial credit covers a small always-on worker like this for a
while; after that it's usage-based billing (typically a few dollars/month
for something this light). Render and Fly.io work similarly if you'd rather
compare pricing.

## Setup (general / self-hosted)

1. **Create the Telegram bot**: message [@BotFather](https://t.me/BotFather)
   on Telegram, run `/newbot`, and copy the token it gives you.
2. **Get your chat ID**: message your new bot once (anything), then visit
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
   find `"chat":{"id": ...}` in the response.
3. **Get a Bitquery API key**: sign up at [bitquery.io](https://bitquery.io)
   (they have a free tier) and grab a key from your account dashboard. This
   project uses Bitquery because it's one of the few providers with a single
   API that covers Solana *and* EVM chains, which multi-chain scanning needs.
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Configure:
   ```bash
   cp .env.example .env
   # then edit .env and fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BITQUERY_API_KEY
   ```
6. Run:
   ```bash
   python main.py
   ```

## Before your first real run — verify the GraphQL queries

The queries in `bitquery_client.py` use Bitquery's cross-chain `Trading` cube
(`Trading.Trades`), built against Bitquery's own current schema reference —
including the specific rules that USD amounts come from `AmountsInUsd.Quote`
(not `.Base`) and that EVM addresses must be lowercase in this cube or you
silently get zero rows back. That said, **this project was built without a
live API key to test against**, so give it one quick check before relying on
it:

1. Open [ide.bitquery.io](https://ide.bitquery.io) (their GraphQL playground).
2. Paste in `RECENT_TRADES_QUERY` and `TOKEN_FIRST_BUYS_QUERY` from
   `bitquery_client.py`, fill in variables (e.g. `network: "Solana"`, any
   real token address for the second one), and run them.
3. If you get a field-not-found error, it names the bad field exactly —
   a quick fix in that one file.

One query is less certain and worth checking specifically:
`TOKEN_HAS_EARLIER_TRADE_QUERY` filters `Block: { Time: { before: $before } }`
to check whether a token has any trade before a cutoff time (this is what
keeps established tokens like stablecoins from being mistaken for new
launches). The `before` operator name for a DateTime field is a guess based
on the pattern of other filters in this schema (`is`/`in`/`notIn`) - if the
IDE rejects it, it's likely called `till`, `lt`, or similar instead; the
error will name the field it doesn't recognize.

This is a 5-minute check the first time; the same queries cover every
chain, so you only need to do it once, not once per chain.

## Tuning

All of these live in `.env`:

| Variable | Meaning |
|---|---|
| `CHAINS` | `all` (default) or comma-separated: `solana,ethereum,base,bsc,arbitrum,optimism,polygon,robinhood,arc` |
| `PROTOCOL_FILTERS` | Optional, scope a chain to one launchpad, e.g. `solana=Pump,arc=Tolly` |
| `NUM_BUYS_TO_CHECK` | How many early buys to sum (default 20) |
| `USD_THRESHOLD` | Dollar bar the sum must clear (default 5000) |
| `POLL_INTERVAL_SECONDS` | How often to poll (default 20s) |
| `WATCH_TIMEOUT_SECONDS` | Give up on a token if it hasn't gotten N buys within this long (default 1800s / 30min) |

## Known limitations / things worth improving

- **"New launch" definition**: right now it treats "recently appeared in a
  DEX trade" as a proxy for "new launch." For higher precision on Solana
  specifically, you could filter directly on pump.fun mint/create events
  rather than trades — Bitquery exposes these too.
- **Rate limits**: Bitquery's free tier has a request budget. Watching many
  tokens across 3+ chains with a 20s poll interval can burn through it
  quickly — widen `POLL_INTERVAL_SECONDS` or reduce `CHAINS` if you hit 429s.
- **No persistence beyond a single machine**: `state.json` is local. If you
  deploy this and redeploy/scale, mount it as a volume or swap `state_store.py`
  for SQLite/Redis.
- **This is a monitoring tool, not trading advice or an auto-trader.** It
  only sends alerts; it doesn't place any trades.

## Files

- `main.py` — entry point, Telegram command handlers, wiring
- `scanner.py` — the watch/evaluate/alert loop
- `bitquery_client.py` — GraphQL queries against Bitquery
- `telegram_notifier.py` — sends the alert message
- `state_store.py` — tiny JSON-backed persistence
- `config.py` — env var loading/validation
