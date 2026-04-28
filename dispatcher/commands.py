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
    "*Approve / reject proposals by number:*\n"
    "  • `approve 1 2 3`    `reject 4 5`    `a 6 8`    `r all`\n"
    "  • Mix in one line: `r 6 a 7 8`\n"
    "  • Typos & \"and\" are fine: `Aprove 1 and 2`\n"
    "  • Bare `approve` / `reject` no longer means all — type `r all` explicitly.\n"
    "  • Reactions on your message:  ✅ done · ⚠️ partial · ❌ nothing matched / parse error\n"
    "\n"
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


APPROVE_VERBS = {"approve", "aprove", "a"}
REJECT_VERBS = {"reject", "rejct", "r"}
# Strip Slack noise (mentions, channel refs, links) before tokenising.
_SLACK_NOISE = re.compile(r"<[@#!][^>]*>|<https?://[^>]+>")


@dataclass
class CommandResult:
    ok: bool
    text: str   # slack-formatted reply
    reaction: str | None = None      # emoji name to reactions.add on user's msg
    quiet_ephemeral: bool = False    # if True, suppress chat_postEphemeral


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

    if first in APPROVE_VERBS | REJECT_VERBS:
        parsed = _parse_decide(text)
        if parsed is None:
            return CommandResult(
                ok=False,
                text=(
                    ":x: Couldn't parse. Examples:\n"
                    "• `approve 1 2 3`   `a 6 8`   `r all`\n"
                    "• mix:  `r 6 a 7 8`\n"
                    "Bare `approve` / `reject` requires `all` or numbers."
                ),
                reaction="x",
            )
        return _exec_decide(parsed)

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


def _parse_decide(text: str) -> list[tuple[str, list[int] | None]] | None:
    """Parse 'r 6 a 7 8' / 'approve all' / etc. into a list of
    (verb, nums_or_None) actions. None for nums means 'all'.
    Returns None if the input is not a decide-style command, OR if the
    parsed actions are empty / mix 'all' with other actions / contain a
    bare verb with no numbers.
    """
    cleaned = _SLACK_NOISE.sub(" ", text).lower()
    tokens = re.findall(r"[a-z]+|\d+", cleaned)
    if not tokens:
        return None

    actions: list[tuple[str, list[int] | None]] = []
    verb: str | None = None
    nums: list[int] = []
    use_all = False

    def flush() -> bool:
        """Append the current verb's action. Returns False on bare-verb
        error (verb set but no nums and no 'all')."""
        nonlocal verb, nums, use_all
        if verb is None:
            return True
        if use_all:
            actions.append((verb, None))
        elif nums:
            actions.append((verb, list(nums)))
        else:
            return False  # bare verb is an error per user decision
        verb, nums, use_all = None, [], False
        return True

    for tok in tokens:
        if tok in APPROVE_VERBS:
            if not flush():
                return None
            verb = "approve"
        elif tok in REJECT_VERBS:
            if not flush():
                return None
            verb = "reject"
        elif tok in ("all", "everything"):
            if verb is None:
                return None  # "all" before any verb is meaningless
            use_all = True
        elif tok.isdigit():
            if verb is None:
                return None  # digits before any verb — not a decide command
            nums.append(int(tok))
        elif tok == "and":
            continue
        else:
            # Unknown word inside a decide command (e.g. "approve please 6").
            # Skip it — already saw a verb, so we're committed to this parser.
            if verb is not None or actions:
                continue
            return None  # bail to other parsers (status/help/etc.)

    if not flush():
        return None
    if not actions:
        return None

    # Disallow mixing 'all' with any other action (ambiguous: "approve all
    # then reject 6" — does 'all' include or exclude #6?). Force the user
    # to be explicit by sending two messages.
    if len(actions) > 1 and any(n is None for _, n in actions):
        return None

    return actions


def _exec_decide(actions: list[tuple[str, list[int] | None]]) -> CommandResult:
    """Map parsed actions to a single `decide` CLI invocation and
    translate its exit code (0/1/2 = full/none/partial) into the
    appropriate reaction emoji + ephemeral behavior."""
    a_nums: list[str] = []
    r_nums: list[str] = []
    a_all = False
    r_all = False
    for verb, nums in actions:
        if verb == "approve":
            if nums is None:
                a_all = True
            else:
                a_nums.extend(str(n) for n in nums)
        else:
            if nums is None:
                r_all = True
            else:
                r_nums.extend(str(n) for n in nums)

    cli_args: list[str] = ["decide"]
    if a_all or a_nums:
        cli_args += ["--approve", "all" if a_all else ",".join(a_nums)]
    if r_all or r_nums:
        cli_args += ["--reject", "all" if r_all else ",".join(r_nums)]

    code, output = _run_ea_cli_raw(cli_args)
    if code == 0:
        return CommandResult(
            ok=True, text=output, reaction="white_check_mark",
            quiet_ephemeral=True,
        )
    if code == 2:
        return CommandResult(
            ok=True, text=output, reaction="warning",
            quiet_ephemeral=False,
        )
    return CommandResult(
        ok=False, text=output or "decide failed", reaction="x",
        quiet_ephemeral=False,
    )


def _run_ea_cli_raw(args: list[str]) -> tuple[int, str]:
    """Like _ea_cli but returns (exit_code, combined_output) so callers
    can interpret structured exit codes (0/1/2 = full/none/partial)
    instead of treating any non-zero as failure."""
    python = config.EVENT_AGGREGATOR_PYTHON
    main_py = config.EVENT_AGGREGATOR_DIR / "main.py"
    if not Path(python).exists() or not main_py.exists():
        return 1, f":warning: event-aggregator not installed at `{config.EVENT_AGGREGATOR_DIR}`"
    try:
        result = subprocess.run(
            [python, str(main_py), *args],
            cwd=str(config.EVENT_AGGREGATOR_DIR),
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return 1, f":hourglass: `{' '.join(args)}` timed out after 120s"
    output = (result.stdout or "").strip()
    if result.stderr:
        output = (output + "\n" + result.stderr.strip()).strip()
    return result.returncode, output
