"""
Decorators + helpers for Job kinds.

`@requires([...])` declares pre-flight dependencies (secrets, files, models).
The consumer validates them before invoking the Job body; failure produces a
specific actionable error rather than a stack trace mid-job.

`@baseline(metric="...", divergence_window="...")` annotates a migrated Job
with the success-signature the migration_verifier compares against. Stored on
the function as `_baseline` for the verifier to introspect.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Baseline:
    metric: str               # e.g. "incidents.jsonl-mtime"
    divergence_window: str    # e.g. "2h", "35m", "80m", "8d"
    cadence_seconds: int = 0  # filled in by @huey.periodic_task wrapper if needed
    description: str = ""

    @property
    def divergence_seconds(self) -> int:
        return _parse_duration(self.divergence_window)


@dataclass
class RequiresSpec:
    items: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Return list of human-readable failure messages; empty list = OK."""
        failures: list[str] = []
        for item in self.items:
            err = _validate_one(item)
            if err:
                failures.append(err)
        return failures


def requires(deps: list[str]) -> Callable:
    """Declare pre-flight dependencies for a Job. Format: `<kind>:<name>`.

    Supported kinds:
      `secret:NAME`       — env var or keychain entry must be set
      `db:relpath.db`     — file must exist under ~/Home-Tools/
      `fs:~/path`         — directory must exist
      `model:name:tag`    — `ollama list` must show the model
      `bin:cmd`           — command must be on PATH
    """
    spec = RequiresSpec(items=list(deps))

    def deco(fn: Callable) -> Callable:
        fn._requires = spec  # type: ignore[attr-defined]

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            failures = spec.validate()
            if failures:
                raise RequirementsNotMet(fn.__name__, failures)
            return fn(*args, **kwargs)

        wrapper._requires = spec  # type: ignore[attr-defined]
        return wrapper

    return deco


def baseline(metric: str, divergence_window: str, description: str = "") -> Callable:
    """Annotate a Job with the @baseline metric the migration_verifier reads.

    The verifier introspects `fn._baseline` on the in-flight Job's callable
    to know what to check after each migration cutover.
    """
    bl = Baseline(metric=metric, divergence_window=divergence_window, description=description)

    def deco(fn: Callable) -> Callable:
        fn._baseline = bl  # type: ignore[attr-defined]
        return fn

    return deco


class RequirementsNotMet(Exception):
    """Raised when @requires pre-flight fails. The message is intentionally
    actionable — names the kind and lists each missing dep so the operator
    can fix it without diving into source."""

    def __init__(self, job_name: str, failures: list[str]) -> None:
        self.job_name = job_name
        self.failures = failures
        super().__init__(
            f"Job {job_name!r} requirements not met:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )


# ── helpers ───────────────────────────────────────────────────────────────────


def _parse_duration(s: str) -> int:
    """Parse '35m', '2h', '80m', '8d' → seconds. Raises on unknown unit."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", s)
    if not m:
        raise ValueError(f"unparseable duration: {s!r} (use e.g. '30s', '5m', '2h', '8d')")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _validate_one(item: str) -> str | None:
    """Return None if the dep is satisfied, else a human-readable error string."""
    if ":" not in item:
        return f"malformed requires entry {item!r}: use 'kind:value'"
    kind, _, value = item.partition(":")
    kind = kind.strip()
    value = value.strip()

    if kind == "secret":
        # Env var first; keychain fallback is the consumer's responsibility.
        if os.environ.get(value):
            return None
        return f"secret {value!r} not set in environment (consumer should unlock keychain first)"

    if kind == "db":
        path = _expand(value)
        if path.exists() and path.is_file():
            return None
        return f"db {value!r} missing at {path}"

    if kind == "fs":
        path = _expand(value)
        if path.exists() and path.is_dir():
            return None
        return f"fs {value!r} missing at {path}"

    if kind == "bin":
        if shutil.which(value):
            return None
        return f"bin {value!r} not on PATH"

    if kind == "model":
        # value like "qwen3:14b" — confirm `ollama list` lists it.
        try:
            out = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, timeout=10,
            ).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return f"model check failed (ollama unavailable): {exc}"
        if value in out:
            return None
        return f"model {value!r} not present (run `ollama pull {value}`)"

    return f"unknown requires kind {kind!r} (supported: secret/db/fs/bin/model)"


def _expand(value: str) -> Path:
    if value.startswith("~"):
        return Path(value).expanduser()
    return Path.home() / "Home-Tools" / value


# ── output_config dispatch helper ─────────────────────────────────────────────


def output_config(target: str, **fields: Any) -> dict:
    """Build a JSON-friendly output_config dict for adapters.dispatch().

    Example:
      output_config("slack", channel="#ian-event-aggregator", text="hi")
      → {"target": "slack", "channel": "#ian-event-aggregator", "text": "hi"}
    """
    return {"target": target, **fields}
