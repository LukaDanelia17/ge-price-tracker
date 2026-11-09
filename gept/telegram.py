"""Send a message to Telegram. Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in
the environment (.env); without them messages are only logged.

    python -m gept.telegram "test message"
"""

import logging
import os
import sys

import httpx

log = logging.getLogger(__name__)

LIMIT = 4000  # Telegram's cap is 4096 characters


def send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.info("Telegram not configured, not sent: %s", text)
        return False
    if len(text) > LIMIT:
        text = text[: LIMIT - 20] + "\n… (truncated)"
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        # The request URL contains the bot token; never log it.
        status = getattr(getattr(e, "response", None), "status_code", None)
        log.error("Telegram send failed (%s)", status or type(e).__name__)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(0 if send(" ".join(sys.argv[1:]) or "ge-price-tracker: test") else 1)
