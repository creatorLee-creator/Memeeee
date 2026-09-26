"""
Sends alert messages to Telegram via the Bot API.
"""
from telegram import Bot
from telegram.constants import ParseMode


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot = Bot(token=bot_token)
        self.chat_id = chat_id

    async def send_launch_alert(
        self,
        chain: str,
        symbol: str,
        name: str,
        address: str,
        total_usd: float,
        num_buys: int,
        explorer_url: str,
    ):
        text = (
            f"🚨 *New launch alert* 🚨\n\n"
            f"*{symbol}* ({name})\n"
            f"Chain: `{chain}`\n"
            f"First {num_buys} buys totaled: *${total_usd:,.0f}*\n"
            f"Contract: `{address}`\n"
            f"[View on explorer]({explorer_url})"
        )
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )

    async def send_html(self, text: str):
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def send_text(self, text: str):
        await self.bot.send_message(chat_id=self.chat_id, text=text)
