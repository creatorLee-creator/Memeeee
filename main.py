"""
Entry point.

Run with:
    python main.py

Requires a .env file (copy .env.example -> .env and fill in values).
"""
import asyncio
import logging

from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

from config import Config
from state_store import StateStore
from bitquery_client import BitqueryClient
from telegram_notifier import TelegramNotifier
from scanner import Scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Memecoin launch scanner is running.\n"
        "I'll alert this chat when a newly launched token's first N buys "
        "add up to your configured USD threshold.\n"
        "Use /status to check current settings."
    )


def make_status_cmd(cfg: Config, state: StateStore):
    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"Chains: {', '.join(cfg.chains)}\n"
            f"Threshold: first {cfg.num_buys_to_check} buys >= ${cfg.usd_threshold:,.0f}\n"
            f"Poll interval: {cfg.poll_interval_seconds}s\n"
            f"Currently watching: {len(state.watching)} token(s)\n"
            f"Already alerted on: {len(state.alerted)} token(s)"
        )
    return status_cmd


async def run():
    cfg = Config.load()
    state = StateStore()

    app = Application.builder().token(cfg.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", make_status_cmd(cfg, state)))

    notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)

    async with app:
        await app.start()
        await app.updater.start_polling()
        log.info("Telegram bot polling started.")

        async with BitqueryClient(
            cfg.bitquery_api_key,
            min_seconds_between_requests=cfg.bitquery_min_seconds_between_requests,
        ) as bitquery:
            scanner = Scanner(
                bitquery=bitquery,
                notifier=notifier,
                state=state,
                chains=cfg.chains,
                num_buys_to_check=cfg.num_buys_to_check,
                usd_threshold=cfg.usd_threshold,
                poll_interval_seconds=cfg.poll_interval_seconds,
                watch_timeout_seconds=cfg.watch_timeout_seconds,
                protocol_filters=cfg.protocol_filters,
                max_checks_per_tick=cfg.max_checks_per_tick,
            )
            try:
                await scanner.run_forever()
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                scanner.stop()
                await app.updater.stop()
                await app.stop()


if __name__ == "__main__":
    asyncio.run(run())
