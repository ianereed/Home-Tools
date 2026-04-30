"""nas-intake watcher entry point — one tick per invocation.

Wired up to LaunchAgent at 5-min StartInterval. Each tick:
  1. Acquire the LOCAL watcher lock (non-blocking) — exit cleanly if another tick is running.
  2. Ensure NAS is mounted (auto-remount via mount-nas.sh if not).
  3. Auto-discover intake/ folders under NAS_ROOT.
  4. For each file: stability gate → dedup → process → journal → archive.
  5. Save state.
"""
from __future__ import annotations

import fcntl
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import discovery
import journal as journal_mod
import nas_mount
import processor
from state import State

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("watcher")


def _file_should_process(p: Path) -> str:
    """Return reason-to-skip string, or 'ok' if processable.
    'defer' = HEIC etc.; 'unsupported' = wrong type entirely.
    """
    if p.is_dir():
        return "is_dir"
    if p.name.startswith(".") or p.name.startswith("._"):
        return "dotfile"
    if p.name.startswith("_") or p.parent.name in {"_processed", "_quarantine", "_review"}:
        return "internal_subfolder"
    suffix = p.suffix.lower()
    if suffix in config.DEFER_EXTS:
        return "defer_v2"  # HEIC etc.
    if suffix not in config.SUPPORTED_EXTS:
        return "unsupported"
    return "ok"


def run_tick() -> int:
    """Run one watcher tick. Returns exit code (0 = clean)."""
    # 1. Acquire lock
    lock_path = config.WATCHER_LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fp = open(lock_path, "w")
    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.info("watcher: another tick is still running — exiting")
        lock_fp.close()
        return 0

    logger.info("watcher: tick start; NAS_ROOT=%s", config.NAS_ROOT)

    # 2. Mount check + auto-remount
    if not nas_mount.ensure_mounted():
        logger.warning("watcher: NAS unavailable — skipping tick")
        lock_fp.close()
        return 0

    # 3. Load state + discover intakes
    state = State()
    state.load()
    intakes = discovery.find_intakes()
    logger.info("watcher: discovered %d intake folder(s)", len(intakes))
    if not intakes:
        state.save()
        lock_fp.close()
        return 0

    processed_count = 0
    skipped_count = 0
    failed_count = 0

    # 4. Per-file pipeline
    for intake in intakes:
        parent = intake.parent
        try:
            entries = list(intake.iterdir())
        except (PermissionError, OSError) as exc:
            logger.warning("watcher: cannot list %s: %s", intake, exc)
            continue

        for entry in entries:
            reason = _file_should_process(entry)
            if reason == "is_dir" or reason == "dotfile" or reason == "internal_subfolder":
                continue
            if reason == "defer_v2":
                logger.warning("watcher: deferring (v2 will handle): %s (%s)", entry.name, entry.suffix)
                skipped_count += 1
                continue
            if reason == "unsupported":
                logger.info("watcher: skipping unsupported file type: %s", entry.name)
                skipped_count += 1
                continue

            # Stability gate
            stability = state.stability_check(entry)
            if stability == "first_sighting":
                logger.info("watcher: stability-gate first sighting — %s", entry.name)
                state.save()
                continue
            if stability == "changed":
                logger.info("watcher: stability-gate file changed (still uploading?) — %s", entry.name)
                state.save()
                continue
            if stability == "unreadable":
                logger.warning("watcher: cannot stat — %s", entry.name)
                continue
            # else: stability == "stable" → proceed

            # SHA dedup
            try:
                sha = processor._sha256_file(entry)
            except OSError as exc:
                logger.warning("watcher: cannot read %s: %s", entry.name, exc)
                continue
            if state.is_processed(sha):
                logger.info("watcher: dedup skip (already processed by sha256) — %s", entry.name)
                # source still here? archive it now to clear the intake
                continue

            # Process!
            try:
                result = processor.process_one(entry, parent, intake, sha, state=state)
            except Exception as exc:
                logger.exception("watcher: unhandled error on %s: %s", entry.name, exc)
                failed_count += 1
                continue

            if result.timed_out:
                n = state.record_timeout(sha)
                logger.warning(
                    "watcher: %s — small-file timeout #%d/%d (%s)",
                    entry.name, n, config.LARGE_FILE_TRIGGER_TIMEOUTS, result.reason,
                )
                state.save()
                failed_count += 1
                continue

            if result.wedged:
                logger.error(
                    "watcher: %s — WEDGED in large-file path: %s",
                    entry.name, result.reason,
                )
                wedged_path = processor._wedge_in_place(
                    entry, result.last_heartbeat, result.reason,
                )
                state.mark_wedged(sha, wedged_path, result.reason,
                                  staging_id=f"local_{sha[:12]}")
                state.forget(entry)  # source moved (renamed)
                state.save()
                # Append a wedged entry to the parent journal so the user sees
                # the abandonment in JOURNAL.md alongside successes.
                try:
                    journal_mod.append(parent, {
                        "ts": (result.last_heartbeat or {}).get("ts")
                              or datetime.now(timezone.utc).isoformat(),
                        "source_name": entry.name,
                        "failure_kind": "wedged",
                        "reason": result.reason,
                        "sha256": sha,
                        "last_heartbeat": result.last_heartbeat,
                    })
                except Exception as exc:
                    logger.exception(
                        "watcher: wedged journal append failed for %s: %s",
                        entry.name, exc,
                    )
                failed_count += 1
                continue

            if not result.ok:
                logger.warning("watcher: %s — process failed: %s", entry.name, result.reason)
                failed_count += 1
                continue

            # Append journal
            try:
                journal_mod.append(parent, result.journal_entry or {})
            except Exception as exc:
                logger.exception("watcher: %s filed but journal append failed: %s", entry.name, exc)
                # Mark processed anyway so we don't reprocess
            state.remember(sha)
            state.forget(entry)  # source has moved; no longer in intake/
            state.clear_timeout(sha)
            state.clear_in_flight_large(sha)
            state.save()
            processed_count += 1
            logger.info("watcher: filed %s → %s", entry.name, result.filed_path)

    state.save()
    lock_fp.close()
    logger.info(
        "watcher: tick end (processed=%d skipped=%d failed=%d)",
        processed_count, skipped_count, failed_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_tick())
