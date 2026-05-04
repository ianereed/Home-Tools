"""
Decorators + helpers for Job kinds.

`@requires([...])` declares pre-flight dependencies (secrets, files, models).
The consumer validates them before invoking the Job body; failure produces a
specific actionable error rather than a stack trace mid-job.

`@baseline(metric="...", divergence_window="...")` annotates a migrated Job
with the success-signature the migration_verifier compares against. Stored on
the function as `_baseline` for the verifier to introspect.

`@requires_model("text"|"vision")` ensures the correct Ollama model is loaded
before a Job runs. Lazy teardown: no unload on return; the next opposite-kind
call triggers the swap. Thread-safe via `_model_state._lock` (RLock).
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Baseline:
    metric: str               # e.g. "incidents.jsonl-mtime"
    divergence_window: str    # e.g. "2h", "35m", "80m", "8d"
    cadence: str = ""         # e.g. "5m", "30m", "1d", "7d" — must match the
                              # @huey.periodic_task crontab. Drives the
                              # verifier's grace period + staleness math.
    description: str = ""

    @property
    def divergence_seconds(self) -> int:
        return _parse_duration(self.divergence_window)

    @property
    def cadence_seconds(self) -> int:
        return _parse_duration(self.cadence) if self.cadence else 3600


def get_baseline(fn) -> Baseline | None:
    """Look up the @baseline metadata on a Job, transparently unwrapping
    huey's TaskWrapper if needed (huey 3.x stores the original function on
    `.func`, so our decorator's attribute lives there)."""
    bl = getattr(fn, "_baseline", None)
    if bl is None and hasattr(fn, "func"):
        bl = getattr(fn.func, "_baseline", None)
    return bl


def get_requires(fn) -> "RequiresSpec | None":
    req = getattr(fn, "_requires", None)
    if req is None and hasattr(fn, "func"):
        req = getattr(fn.func, "_requires", None)
    return req


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


def baseline(metric: str, divergence_window: str, cadence: str = "", description: str = "") -> Callable:
    """Annotate a Job with the @baseline metric the migration_verifier reads.

    The verifier introspects `fn._baseline` on the in-flight Job's callable
    to know what to check after each migration cutover.

    `cadence` should match the @huey.periodic_task crontab — it tells the
    verifier how long to grace-skip before judging a missing baseline as
    failure. Defaults to 1h if omitted (loose default; declare explicitly).
    """
    bl = Baseline(
        metric=metric,
        divergence_window=divergence_window,
        cadence=cadence,
        description=description,
    )

    def deco(fn: Callable) -> Callable:
        fn._baseline = bl  # type: ignore[attr-defined]
        return fn

    return deco


def migrates_from(plist_label: str) -> Callable:
    """Pin the LaunchAgent label this kind replaces.

    Kind name → label is otherwise inferred as `com.home-tools.<kind>`
    with `_` → `-`, but real labels diverge (e.g. nas_intake_scan
    replaces `com.home-tools.nas-intake`; health_collect replaces
    `com.health-dashboard.collect`). This decorator pins the actual
    label so `cli migrate <kind>` finds the right plist.
    """
    def deco(fn: Callable) -> Callable:
        fn._plist_label = plist_label  # type: ignore[attr-defined]
        return fn
    return deco


def get_plist_label(fn) -> str | None:
    label = getattr(fn, "_plist_label", None)
    if label is None and hasattr(fn, "func"):
        label = getattr(fn.func, "_plist_label", None)
    return label


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


# ── @requires_model primitive ─────────────────────────────────────────────────

_MODEL_SWAP_LOG = Path.home() / "Home-Tools" / "logs" / "model_swaps.jsonl"


def _parse_keep_alive(raw: str):
    """Parse "-1" → int(-1), "30s"/"10m" → str (Ollama accepts both forms)."""
    raw = (raw or "").strip()
    try:
        return int(raw)
    except (ValueError, TypeError):
        return raw


class _ModelState:
    """Process-wide singleton tracking the Ollama model currently loaded.

    Model names and contexts are read from environment variables at call time
    so the primitive works in the jobs consumer (which sets them in its plist)
    without a hard import from event-aggregator/config.py.

    Concurrency model — IMPORTANT for any future @requires_model kind:

      The RLock (self._lock) protects only the *swap mechanism* (load/unload
      bookkeeping in swap_to/ensure and the _batch_kinds set). It is RELEASED
      before the decorated function body runs (see requires_model wrapper).

      The actual concurrency guard for "no other kind swaps the model while
      I'm using it" is the consumer running with `-w 1 -k thread` in
      jobs/run-consumer.sh: a single huey worker thread serializes all model
      kinds. Two text/vision kinds cannot run simultaneously by construction.

      If you ever raise the worker count (-w >1) for a kind that uses
      @requires_model, you MUST add per-kind locking that's held across the
      function body — otherwise thread B can swap models out from under
      thread A's in-flight subprocess. This is why -w 1 is intentional.

    Lazy teardown: swap_to() is called only when the requested kind differs
    from the currently loaded model. No unload happens on return from the
    decorated function.

    Batch hint: while _batch_kinds is non-empty (added by requires_model with
    batch_hint="drain")), calls to ensure() for kinds NOT in _batch_kinds are
    silently deferred. The batch entry clears when the outer function returns,
    after which the next opposite-kind call proceeds normally.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current: str | None = None
        self._batch_kinds: set[str] = set()

    # ── config from env ────────────────────────────────────────────────────────

    @property
    def text_model(self) -> str:
        return os.environ.get("OLLAMA_MODEL", "qwen3:14b")

    @property
    def vision_model(self) -> str:
        return os.environ.get("LOCAL_VISION_MODEL", "qwen2.5vl:7b")

    @property
    def ollama_url(self) -> str:
        return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    @property
    def text_ctx(self) -> int:
        return int(os.environ.get("OLLAMA_NUM_CTX_TEXT", "16384"))

    @property
    def vision_ctx(self) -> int:
        return int(os.environ.get("OLLAMA_NUM_CTX_VISION", "16384"))

    @property
    def text_keep_alive(self):
        return _parse_keep_alive(os.environ.get("OLLAMA_KEEP_ALIVE_TEXT", "-1"))

    @property
    def vision_keep_alive(self):
        return _parse_keep_alive(os.environ.get("OLLAMA_KEEP_ALIVE_VISION", "30s"))

    def model_for(self, kind: str) -> str:
        if kind == "text":
            return self.text_model
        if kind == "vision":
            return self.vision_model
        raise ValueError(f"unknown model kind {kind!r} (expected 'text' or 'vision')")

    def _http_post(self, url: str, payload: dict, timeout: int = 120) -> None:
        """POST JSON to url. Extracted for easy monkeypatching in tests."""
        import urllib.request as _urllib
        data = json.dumps(payload).encode()
        req = _urllib.Request(url, data=data, headers={"Content-Type": "application/json"})
        with _urllib.urlopen(req, timeout=timeout):
            pass

    def swap_to(self, kind: str) -> None:
        """Unload current model (if any) and warmup the target kind.
        Caller MUST hold self._lock.
        Records the swap to model_swaps.jsonl for telemetry.
        """
        target = self.model_for(kind)
        if self._current == target:
            return

        from_model = self._current
        t0 = time.monotonic()

        if from_model is not None:
            try:
                self._http_post(
                    f"{self.ollama_url}/api/generate",
                    {"model": from_model, "keep_alive": 0, "prompt": "", "stream": False},
                    timeout=10,
                )
                logger.info("model_state: unloaded %s", from_model)
            except Exception as exc:
                logger.warning("model_state: unload %s failed (best-effort): %s", from_model, exc)

        ctx = self.text_ctx if kind == "text" else self.vision_ctx
        ka = self.text_keep_alive if kind == "text" else self.vision_keep_alive
        warmup_ok = False
        try:
            self._http_post(
                f"{self.ollama_url}/api/generate",
                {"model": target, "prompt": "", "stream": False,
                 "keep_alive": ka, "options": {"num_ctx": ctx}},
                timeout=120,
            )
            logger.info("model_state: warmed %s (ctx=%d)", target, ctx)
            warmup_ok = True
        except Exception as exc:
            logger.warning("model_state: warmup %s failed (best-effort): %s", target, exc)

        latency_ms = int((time.monotonic() - t0) * 1000)
        # Only commit the loaded-model cache on confirmed warmup success.
        # On failure, set to None so the next ensure() retries instead of no-oping.
        self._current = target if warmup_ok else None
        record_swap(from_model or "none", target, latency_ms)

    def ensure(self, kind: str) -> None:
        """Ensure the named model kind is loaded. Caller MUST hold self._lock.
        If a batch of the opposite kind is active, this call is deferred
        (silently returns without swapping).
        """
        target = self.model_for(kind)
        if self._current == target:
            return
        if self._batch_kinds and kind not in self._batch_kinds:
            return  # deferred; next ensure() after batch clears will swap
        self.swap_to(kind)


_model_state = _ModelState()


def record_swap(from_model: str, to_model: str, latency_ms: int) -> None:
    """Append a model-swap telemetry record to model_swaps.jsonl."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "from": from_model,
        "to": to_model,
        "latency_ms": latency_ms,
    }
    try:
        _MODEL_SWAP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_MODEL_SWAP_LOG, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def requires_model(kind: str, batch_hint: str = "") -> Callable:
    """Ensure the named Ollama model kind is loaded before calling the decorated fn.

    kind: "text" or "vision"
    batch_hint="drain": while this function executes, opposite-kind swap requests
    are deferred. Preserves warmup amortization when the same kind is called
    repeatedly (e.g. draining a text queue before switching to vision).

    Lazy teardown: no unload on return. The next call to requires_model with the
    opposite kind triggers the swap.

    NOTE: _model_state._lock is acquired ONLY around ensure()/_batch_kinds
    bookkeeping, not across fn(). The single-threaded huey consumer
    (`-w 1 -k thread` in jobs/run-consumer.sh) is what prevents concurrent
    model swaps during fn(). See _ModelState docstring for the full
    concurrency model and what changes if you raise the worker count.
    """
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with _model_state._lock:
                if batch_hint == "drain":
                    _model_state._batch_kinds.add(kind)
                _model_state.ensure(kind)
            try:
                return fn(*args, **kwargs)
            finally:
                if batch_hint == "drain":
                    with _model_state._lock:
                        _model_state._batch_kinds.discard(kind)
        return wrapper
    return deco


# ── output_config dispatch helper ─────────────────────────────────────────────


def output_config(target: str, **fields: Any) -> dict:
    """Build a JSON-friendly output_config dict for adapters.dispatch().

    Example:
      output_config("slack", channel="#ian-event-aggregator", text="hi")
      → {"target": "slack", "channel": "#ian-event-aggregator", "text": "hi"}
    """
    return {"target": target, **fields}
