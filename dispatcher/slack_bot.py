"""
Slack Socket Mode listener — lives in two channels:

  INTERACTIVE_CHANNEL (default #ian-event-aggregator)
    Parses commands and shells out to event-aggregator's CLI.

  IMAGE_INTAKE_CHANNEL (default #ian-image-intake)
    Receives image/PDF uploads, classifies locally (qwen2.5vl via Ollama),
    routes to finance-monitor/intake/, nas-staging/, or invokes
    event-aggregator ingest-image. In-thread `!route <category>` overrides.
"""
from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import classifier
import commands
import config
import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_IMAGE_PDF_MIMES = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/gif",
    "image/webp", "image/heic", "image/heif", "image/tiff",
    "application/pdf",
})

# Remember what we've already routed so `!route <cat>` in a thread can find
# the original file. Key: Slack message ts; value: {"path": final_path,
# "classification": Classification, "channel": <id>}.
_routed_history: dict[str, dict] = {}

_channel_id_cache: dict[str, str] = {}


def _resolve_channel_name(app: App, name: str) -> str | None:
    if not name:
        return None
    if name in _channel_id_cache:
        return _channel_id_cache[name]
    raw = name.lstrip("#")
    if raw and raw[0].upper() in "CDGW" and raw[1:].replace("_", "").isalnum():
        _channel_id_cache[name] = raw
        return raw
    cursor = None
    while True:
        kwargs = {"exclude_archived": True, "limit": 200, "types": "public_channel,private_channel"}
        if cursor:
            kwargs["cursor"] = cursor
        result = app.client.conversations_list(**kwargs)
        for ch in result.get("channels", []):
            if ch.get("name") == raw:
                _channel_id_cache[name] = ch["id"]
                return ch["id"]
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return None


def _is_authorized(user_id: str) -> bool:
    if not config.ALLOWED_SLACK_USER_IDS:
        return True
    return user_id in config.ALLOWED_SLACK_USER_IDS


def _download_to_tmp(url: str, filename: str) -> Path:
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    # Slack supplies the filename as user-controlled input. Strip everything
    # outside a conservative ASCII set and bound length to keep downstream
    # filesystem / argv / Slack-rendered paths predictable.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120] or "upload"
    stamp = str(int(time.time()))
    dest = config.TMP_DIR / f"{stamp}_{safe}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
        timeout=60,
    )
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def run() -> None:
    problems = config.validate()
    if problems:
        for p in problems:
            logger.error("dispatcher: %s", p)
        raise RuntimeError("dispatcher config invalid — see errors above")

    if not config.ALLOWED_SLACK_USER_IDS:
        logger.warning(
            "dispatcher: ALLOWED_SLACK_USER_IDS is empty — "
            "any workspace member can trigger commands"
        )

    app = App(token=config.SLACK_BOT_TOKEN)

    interactive_id = _resolve_channel_name(app, config.INTERACTIVE_CHANNEL)
    intake_id = _resolve_channel_name(app, config.IMAGE_INTAKE_CHANNEL)
    if not interactive_id:
        logger.error("Could not resolve INTERACTIVE_CHANNEL=%r", config.INTERACTIVE_CHANNEL)
    if not intake_id:
        logger.error("Could not resolve IMAGE_INTAKE_CHANNEL=%r", config.IMAGE_INTAKE_CHANNEL)

    logger.info(
        "dispatcher: interactive=%s (%s), intake=%s (%s)",
        config.INTERACTIVE_CHANNEL, interactive_id,
        config.IMAGE_INTAKE_CHANNEL, intake_id,
    )

    @app.event("message")
    def handle_message(event, say, client, logger):
        channel = event.get("channel")
        subtype = event.get("subtype")
        if event.get("bot_id") or subtype == "bot_message":
            return
        user = event.get("user") or ""
        text = (event.get("text") or "").strip()
        files = event.get("files") or []
        thread_ts = event.get("thread_ts")

        if not _is_authorized(user):
            logger.warning("dispatcher: rejected from unauthorized user %s in %s", user, channel)
            return

        if channel == interactive_id:
            # Tier 3.2: any non-bot top-level message in the interactive
            # channel buries the dashboard. Bump the burial counter so
            # event-aggregator's next render decides whether to repost.
            if not thread_ts:
                try:
                    commands.handle("bump-dashboard")
                except Exception as exc:
                    logger.debug("dispatcher: bump-dashboard failed: %s", exc)
            if not files:
                _handle_interactive(text, say, event.get("ts"), client, channel)
                return

        if channel == intake_id:
            if files:
                _handle_intake_upload(event, client, channel)
                return
            if thread_ts and text.lower().startswith("!route"):
                _handle_route_override(text, thread_ts, client, channel)
                return
            if text and not thread_ts:
                # Top-level text in the intake channel — reply with a hint so users
                # know where commands belong.
                say(
                    text=(
                        "This channel is for image/PDF uploads. "
                        f"Post commands in <#{interactive_id}> instead."
                    ),
                    thread_ts=event.get("ts"),
                )

    @app.action("ea_approve")
    def handle_ea_approve(ack, body, logger):
        ack()
        try:
            num = body["actions"][0]["value"]
            result = commands.handle(f"approve {num}")
            if result and not result.ok:
                logger.warning("ea_approve failed for #%s: %s", num, result.text)
        except Exception as exc:
            logger.warning("ea_approve handler error: %s", exc)

    @app.action("ea_reject")
    def handle_ea_reject(ack, body, logger):
        ack()
        try:
            num = body["actions"][0]["value"]
            result = commands.handle(f"reject {num}")
            if result and not result.ok:
                logger.warning("ea_reject failed for #%s: %s", num, result.text)
        except Exception as exc:
            logger.warning("ea_reject handler error: %s", exc)

    @app.action("ea_swap_wait")
    def handle_ea_swap_wait(ack, body, logger):
        ack()
        try:
            decision_id = body["actions"][0]["value"]
            result = commands.handle(f"swap --decision-id {decision_id} --decision wait")
            if result and not result.ok:
                logger.warning("ea_swap_wait failed for %s: %s", decision_id, result.text)
        except Exception as exc:
            logger.warning("ea_swap_wait handler error: %s", exc)

    @app.action("ea_swap_interrupt")
    def handle_ea_swap_interrupt(ack, body, logger):
        ack()
        try:
            decision_id = body["actions"][0]["value"]
            result = commands.handle(f"swap --decision-id {decision_id} --decision interrupt")
            if result and not result.ok:
                logger.warning("ea_swap_interrupt failed for %s: %s", decision_id, result.text)
        except Exception as exc:
            logger.warning("ea_swap_interrupt handler error: %s", exc)

    @app.event("file_shared")
    def handle_file_shared(event, client, logger):
        # We handle the full message event above (which includes files[]),
        # so file_shared only matters if the file lands in a message we
        # missed. Slack delivers both events; handle once via message handler.
        return

    logger.info("dispatcher: starting Socket Mode handler")
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    handler.start()


def _handle_interactive(text: str, say, msg_ts: str, client, channel: str) -> None:
    result = commands.handle(text)
    if result is None:
        # Not a command — silently ignore so we don't chatter on every message.
        return
    say(text=result.text)


def _handle_intake_upload(event, client, channel: str) -> None:
    msg_ts = event.get("ts")
    files = [f for f in event.get("files", []) if f.get("mimetype") in _IMAGE_PDF_MIMES]
    if not files:
        return

    # Immediate ack so the user sees progress (local classification takes 30–60s).
    names = ", ".join(f.get("name", "file") for f in files)
    try:
        client.chat_postMessage(
            channel=channel,
            thread_ts=msg_ts,
            text=f":mag: Received *{names}*, classifying locally…",
        )
    except Exception as exc:
        logger.warning("dispatcher: failed to post ack: %s", exc)

    # Download + route each file independently. Multi-file messages are treated
    # as separate documents for now; multi-page bundling can come later.
    for f in files:
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            _reply(client, channel, msg_ts, f":warning: no download URL for `{f.get('name', 'file')}`")
            continue
        try:
            local = _download_to_tmp(url, f.get("name") or "upload")
        except Exception as exc:
            _reply(client, channel, msg_ts, f":x: failed to download `{f.get('name')}`: {exc}")
            continue

        cls = classifier.classify(local)
        if cls.error:
            _reply(
                client, channel, msg_ts,
                f":x: classification failed for `{f.get('name')}`: {cls.error}\n"
                f"File left in `tmp/`. Use `!route <category>` to force placement."
            )
            _routed_history[msg_ts] = {
                "path": local, "classification": cls, "channel": channel,
            }
            continue

        # Events category — hand off to event-aggregator's ingest-image CLI.
        if cls.category == "Events":
            ok, msg = router.ingest_as_event(local)
            if ok:
                _reply(
                    client, channel, msg_ts,
                    f":calendar: *Events* → event-aggregator ingesting `{f.get('name')}`\n"
                    f"{msg}"
                )
            else:
                _reply(
                    client, channel, msg_ts,
                    f":x: event-aggregator ingest failed: {msg}\n"
                    f"File left in `tmp/`. Use `!route <category>` to force placement."
                )
                _routed_history[msg_ts] = {
                    "path": local, "classification": cls, "channel": channel,
                }
            continue

        try:
            result = router.route(
                local, cls,
                slack_thread={"channel": channel, "thread_ts": msg_ts},
            )
        except Exception as exc:
            _reply(client, channel, msg_ts, f":x: routing failed: {exc}")
            continue

        _routed_history[msg_ts] = {
            "path": result.destination,
            "classification": cls,
            "channel": channel,
        }

        conf_note = "low confidence" if result.was_low_confidence else f"confidence {cls.confidence:.2f}"
        hint = ""
        if result.was_low_confidence:
            hint = "\n_Reply `!route <category>` in this thread to move it (e.g. `!route Financial`)._"
        _reply(
            client, channel, msg_ts,
            f":white_check_mark: *{result.category}* ({conf_note}) → "
            f"`{_display_path(result.destination)}`{hint}"
        )


def _handle_route_override(text: str, thread_ts: str, client, channel: str) -> None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        _reply(client, channel, thread_ts, "Usage: `!route <category>` (e.g. `!route Financial`)")
        return
    category = parts[1].strip()
    state = _routed_history.get(thread_ts)
    if not state:
        _reply(
            client, channel, thread_ts,
            ":warning: don't have routing history for this thread — rerouting is only supported "
            "while the dispatcher process that received the upload is still running."
        )
        return

    src = Path(state["path"])
    if not src.exists():
        _reply(client, channel, thread_ts, f":warning: source file missing at `{src}` — can't reroute")
        return

    try:
        new_result = router.route(
            src, state["classification"],
            override=category,
            slack_thread={"channel": channel, "thread_ts": thread_ts},
        )
    except Exception as exc:
        _reply(client, channel, thread_ts, f":x: reroute failed: {exc}")
        return

    _routed_history[thread_ts] = {
        "path": new_result.destination,
        "classification": state["classification"],
        "channel": channel,
    }
    _reply(
        client, channel, thread_ts,
        f":arrows_counterclockwise: rerouted to *{new_result.category}* → "
        f"`{_display_path(new_result.destination)}`"
    )


def _reply(client, channel: str, thread_ts: str, text: str) -> None:
    try:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
    except Exception as exc:
        logger.warning("dispatcher: reply failed: %s", exc)


def _display_path(p: Path) -> str:
    """Shorten long absolute paths for Slack display."""
    s = str(p)
    home = str(Path.home())
    if s.startswith(home):
        return "~" + s[len(home):]
    return s
