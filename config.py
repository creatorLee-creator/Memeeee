"""
Loads and validates configuration from environment variables / .env file.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


# Every chain this project knows how to query (see NETWORK_DISPLAY_NAMES in
# bitquery_client.py). "all" in CHAINS expands to this list.
ALL_CHAINS = ["solana", "ethereum", "base", "bsc", "arbitrum", "optimism", "polygon", "robinhood", "arc"]


def _get_list(raw: str) -> list[str]:
    return [c.strip().lower() for c in raw.split(",") if c.strip()]


def _get_protocol_filters(raw: str) -> dict[str, str]:
    """Parses 'solana=Pump,arc=Tolly' into {'solana': 'Pump', 'arc': 'Tolly'}."""
    out = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        chain, family = pair.split("=", 1)
        out[chain.strip().lower()] = family.strip()
    return out


@dataclass
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    bitquery_api_key: str
    chains: list[str] = field(default_factory=list)
    protocol_filters: dict = field(default_factory=dict)
    num_buys_to_check: int = 20
    usd_threshold: float = 5000.0
    poll_interval_seconds: int = 20
    watch_timeout_seconds: int = 1800
    bitquery_min_seconds_between_requests: float = 3.0
    max_checks_per_tick: int = 5

    @classmethod
    def load(cls) -> "Config":
        missing = []
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        api_key = os.getenv("BITQUERY_API_KEY", "")

        if not token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if not api_key:
            missing.append("BITQUERY_API_KEY")

        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill them in."
            )

        chains = _get_list(os.getenv("CHAINS", "all"))
        if "all" in chains:
            chains = list(ALL_CHAINS)

        protocol_filters = _get_protocol_filters(os.getenv("PROTOCOL_FILTERS", ""))

        return cls(
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            bitquery_api_key=api_key,
            chains=chains,
            protocol_filters=protocol_filters,
            num_buys_to_check=int(os.getenv("NUM_BUYS_TO_CHECK", "20")),
            usd_threshold=float(os.getenv("USD_THRESHOLD", "5000")),
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "20")),
            watch_timeout_seconds=int(os.getenv("WATCH_TIMEOUT_SECONDS", "1800")),
            bitquery_min_seconds_between_requests=float(
                os.getenv("BITQUERY_MIN_SECONDS_BETWEEN_REQUESTS", "3.0")
            ),
            max_checks_per_tick=int(os.getenv("MAX_CHECKS_PER_TICK", "5")),
        )
