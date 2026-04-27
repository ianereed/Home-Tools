"""
Interactive command handling for ian-event-aggregator.

The dispatcher parses the first token of each message and shells out to
event-aggregator's CLI subcommands. Keeps all state mutation inside the
event-aggregator project.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import config

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "*Available commands:*\n"
    "• `approve` / `approve 1,3` — approve proposal by number\n"
    "• `reject` / `reject 2` — reject proposal by number\n"
    "• `add: <description>` — manual event entry, e.g. `add: dinner with Bryan Sat 7pm`\n"
    "• `status` — show last run, pending count, ollama health\n"
    "• `last run` — show summary of the most recent run\n"
    "• `pending` — list pending proposals\n"
    "• `what's on <timeframe>` — ask about upcoming events, e.g. `what's on friday`\n"
    "• `conflicts <timeframe>` — check for overlaps\n"
    "• `changes` / `changes since <when>` — recent activity (default last 24h; `<when>` accepts `1d`, `12h`, `30m`, ISO date)\n"
    "• `watch` / `watch <channel>` / `mute <channel>` — list/toggle event-aggregator's monitored Slack channels\n"
    "• `force scan` — kick off an event-aggregator run immediately\n"
    "• `undo last` — delete the most recently written GCal event\n"
    "• `help` / `?` — show this message\n"
)


@dataclass
class CommandResult:
    ok: bool
    text: str   # slack-formatted reply


def handle(raw_text: str) -> CommandResult | None:
    """Parse and execute a command. Returns None if the message isn't a command."""
    text = (raw_text or "").strip()
    if not text:
        return None

    lower = text.lower()
    first = lower.split(maxsplit=1)[0]
    rest = text[len(first):].strip()

    if first in ("help", "?"):
        return CommandResult(ok=True, text=HELP_TEXT)

    if first == "approve":
        return _ea_cli(["approve", "--nums", rest] if rest else ["approve"])

    if first == "reject":
        return _ea_cli(["reject", "--nums", rest] if rest else ["reject"])

    if first == "swap":
        decision = rest.lower().strip()
        if decision not in ("wait", "interrupt"):
            return CommandResult(ok=False, text="Usage: `swap wait` or `swap interrupt`")
        # Resolve the current pending swap decision from state.json
        import json as _json
        state_path = config.EVENT_AGGREGATOR_DIR / "state.json"
        try:
            data = _json.loads(state_path.read_text())
            decisions = data.get("swap_decisions", {})
            pending_ids = [did for did, info in decisions.items() if info.get("decision") == "pending"]
            if not pending_ids:
                return CommandResult(ok=False, text=":x: no pending swap decision found")
            return _ea_cli(["swap", "--decision-id", pending_ids[0], "--decision", decision])
        except Exception as exc:
            return CommandResult(ok=False, text=f":x: swap lookup failed: {exc}")

    if first == "status":
        return _ea_cli(["status", "--json"])

    if first == "pending":
        return _ea_cli(["status", "--pending"])

    # Two-token command: "last run"
    if lower.startswith("last run"):
        return _ea_cli(["status", "--last-run"])

    # Two-token command: "what's on <timeframe>"
    m = re.match(r"what'?s\s+on\s+(.+)", text, re.IGNORECASE)
    if m:
        return _ea_cli(["query", "--calendar", m.group(1).strip()])

    if first == "conflicts":
        return _ea_cli(["query", "--conflicts", rest or "this week"])

    # add: <description>
    if lower.startswith("add:"):
        description = text.split(":", 1)[1].strip()
        if not description:
            return CommandResult(ok=False, text="Usage: `add: <event description>`")
        return _ea_cli(["add-event", "--text", description])

    # changes [since <when>]
    if first == "changes":
        m = re.match(r"changes\s+since\s+(.+)", text, re.IGNORECASE)
        if m:
            return _ea_cli(["changes", "--since", m.group(1).strip()])
        return _ea_cli(["changes"])  # defaults to 1d

    # watch (list) / watch <channel> / mute <channel>
    if first == "watch":
        if not rest:
            return _ea_cli(["config", "--list-channels"])
        return _ea_cli(["config", "--watch", rest])

    if first == "mute":
        if not rest:
            return CommandResult(ok=False, text="Usage: `mute <channel-name>`")
        return _ea_cli(["config", "--mute", rest])

    # force scan
    if lower.startswith("force scan") or lower == "force":
        return _force_scan()

    # undo last
    if lower.startswith("undo last") or lower == "undo":
        return _ea_cli(["undo-last"])

    # Not a recognized command — let the caller decide (usually: ignore).
    return None


def _force_scan() -> CommandResult:
    """Trigger an immediate event-aggregator launchd run via launchctl kickstart."""
    label = "com.home-tools.event-aggregator"
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as exc:
        return CommandResult(ok=False, text=f":x: kickstart failed: {exc}")
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[:300]
        return CommandResult(
            ok=False,
            text=f":x: `launchctl kickstart {label}` exited {result.returncode}: `{tail}`",
        )
    return CommandResult(
        ok=True,
        text=f":zap: kicked off `{label}` — check the log in ~30s for results",
    )


def _ea_cli(args: list[str]) -> CommandResult:
    """Invoke event-aggregator's CLI and format the output for Slack."""
    python = config.EVENT_AGGREGATOR_PYTHON
    main_py = config.EVENT_AGGREGATOR_DIR / "main.py"

    if not Path(python).exists() or not main_py.exists():
        return CommandResult(
            ok=False,
            text=f":warning: event-aggregator not installed at `{config.EVENT_AGGREGATOR_DIR}`",
        )

    try:
        result = subprocess.run(
            [python, str(main_py), *args],
            cwd=str(config.EVENT_AGGREGATOR_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(ok=False, text=f":hourglass: `{' '.join(args)}` timed out after 120s")

    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout or "").splitlines()[-10:])
        return CommandResult(
            ok=False,
            text=f":x: `{' '.join(args)}` failed (exit {result.returncode}):\n```\n{tail}\n```",
        )

    stdout = (result.stdout or "").strip()
    if not stdout:
        return CommandResult(ok=True, text=":white_check_mark: done")
    return CommandResult(ok=True, text=stdout)
