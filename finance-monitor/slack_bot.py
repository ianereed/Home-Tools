"""
Slack Finance Bot — listens for DMs and answers finance questions locally.

Uses Socket Mode (outbound WebSocket — no inbound port binding required).
Only responds to direct messages; ignores all channel messages for privacy.

Credentials stored in .env:
  SLACK_APP_TOKEN=xapp-...   (Socket Mode App-Level Token)
  SLACK_BOT_TOKEN=xoxb-...   (Bot User OAuth Token)
"""
import logging
import sys
import time

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import config
import db
import query_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_RATE_LIMIT_SECONDS = 60
_user_last_query: dict[str, float] = {}


def run() -> None:
    db.init_db()

    if not config.SLACK_APP_TOKEN:
        raise RuntimeError("SLACK_APP_TOKEN not set — add it to .env")
    if not config.SLACK_BOT_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN not set — add it to .env")

    if not config.ALLOWED_SLACK_USER_IDS:
        logger.warning(
            "finance-bot: ALLOWED_SLACK_USER_IDS is not set — "
            "any workspace member can DM the bot and query your financial data"
        )

    bot_token = config.SLACK_BOT_TOKEN
    app_token = config.SLACK_APP_TOKEN

    app = App(token=bot_token)

    @app.event("message")
    def handle_message(event, say, logger):
        # DMs only — channel messages are visible to others in the workspace
        if event.get("channel_type") != "im":
            return
        # Skip bot messages (echoes of our own replies)
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        question = (event.get("text") or "").strip()
        if not question:
            return

        sender = event.get("user", "")
        if config.ALLOWED_SLACK_USER_IDS and sender not in config.ALLOWED_SLACK_USER_IDS:
            logger.warning("finance-bot: rejected DM from unauthorized user %s", sender)
            say("Sorry, you're not authorized to use this bot.")
            return

        now = time.monotonic()
        elapsed = now - _user_last_query.get(sender, 0)
        if elapsed < _RATE_LIMIT_SECONDS:
            remaining = int(_RATE_LIMIT_SECONDS - elapsed)
            say(f"_Please wait {remaining}s before asking another question._")
            return
        _user_last_query[sender] = now

        logger.info("finance-bot: received DM from %s (len=%d)", sender, len(question))

        # Acknowledge immediately so the user knows we're working
        thinking_resp = say("_Thinking..._")

        answer = query_engine.answer(question)

        # Replace the "Thinking..." message with the real answer
        app.client.chat_update(
            channel=event["channel"],
            ts=thinking_resp["ts"],
            text=answer,
        )
        logger.info("finance-bot: answered DM for %s", sender)

    logger.info("finance-bot: starting Socket Mode handler")
    handler = SocketModeHandler(app, app_token)
    handler.start()
