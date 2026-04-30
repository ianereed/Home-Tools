"""Per-parent journal: human-and-machine-readable record of every filed item.

Two files updated together per parent:
- <parent>/JOURNAL.md   — append-only Markdown, one H2 entry per filing
- <parent>/journal.jsonl — one JSON object per line

Both files live ON the NAS (so phone, laptop, mini all see them). Atomicity is
achieved by writing temp files locally first, then renaming. A LOCAL fcntl
lock on locks/<parent-sha>.lock prevents same-parent concurrency on the mini
(mini is the only writer; SMB locks are unreliable so we don't use them).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def _parent_lock_path(parent: Path) -> Path:
    config.LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(parent).encode("utf-8")).hexdigest()[:8]
    return config.LOCKS_DIR / f"{digest}.lock"


def _md_entry(entry: dict) -> str:
    """Render a journal entry as a Markdown H2 block. Branches on
    `failure_kind`: success entries render filing details; failure entries
    (currently only "wedged") render the abandonment reason instead.
    """
    failure_kind = entry.get("failure_kind") or ""
    if failure_kind == "wedged":
        return _md_wedged_entry(entry)

    title = entry.get("title") or entry.get("source_name") or "Untitled"
    doc_date = entry.get("doc_date") or "unknown"
    doc_type = entry.get("doc_type") or "general"
    rel = entry.get("filed_rel") or entry.get("filed_path") or ""
    source_name = entry.get("source_name") or ""
    confidence = entry.get("confidence", 0.0)
    summary = entry.get("summary") or ""
    ts = entry.get("ts") or datetime.utcnow().isoformat() + "Z"

    lines = [
        f"## {doc_date} — {title}",
        "",
        f"- **Doc type**: {doc_type}",
        f"- **Filed**: [{rel}](./{rel})" if rel else "- **Filed**: (unknown)",
        f"- **Source**: dropped in `intake/{source_name}` at {ts}",
        f"- **Confidence**: {confidence:.2f}" if isinstance(confidence, (int, float)) else f"- **Confidence**: {confidence}",
        f"- **Summary**: {summary}",
        "",
    ]
    return "\n".join(lines)


def _md_wedged_entry(entry: dict) -> str:
    """Render a 'wedged' (large-file pipeline gave up after hangup) entry."""
    source_name = entry.get("source_name") or "(unknown)"
    reason = entry.get("reason") or "no progress signal — see diagnostic log"
    ts = entry.get("ts") or datetime.utcnow().isoformat() + "Z"
    last_hb = entry.get("last_heartbeat") or {}
    phase = last_hb.get("phase", "(unknown)") if isinstance(last_hb, dict) else "(unknown)"
    page_done = last_hb.get("page_done", "?") if isinstance(last_hb, dict) else "?"
    page_total = last_hb.get("page_total", "?") if isinstance(last_hb, dict) else "?"

    lines = [
        f"## ⚠️ WEDGED — {source_name}",
        "",
        f"- **At**: {ts}",
        f"- **Reason**: {reason}",
        f"- **Last phase**: {phase} (page {page_done}/{page_total})",
        f"- **Source**: renamed in place to `_WEDGED_{source_name}`. "
        f"See sibling `_WEDGED_{source_name}.diagnostic.log` and the per-file "
        f"trace at `~/Library/Logs/home-tools-nas-intake-large/<sha12>.log` on the mini.",
        "",
    ]
    return "\n".join(lines)


def append(parent: Path, entry: dict) -> None:
    """Atomically append `entry` to <parent>/JOURNAL.md and <parent>/journal.jsonl.

    JSONL is the canonical, machine-readable record (it's renamed first). MD is
    a regenerated friendly view that mirrors the latest entry. If MD rename
    fails post-JSONL-rename, JSONL stays correct and we log loudly.
    """
    parent.mkdir(parents=True, exist_ok=True)
    md_path = parent / "JOURNAL.md"
    jl_path = parent / "journal.jsonl"

    lock_path = _parent_lock_path(parent)
    with open(lock_path, "w") as lock_fp:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            logger.warning("journal.append: cannot acquire lock %s: %s", lock_path, exc)

        # Build new JSONL content: existing + new line
        jl_old = jl_path.read_text(encoding="utf-8") if jl_path.exists() else ""
        if jl_old and not jl_old.endswith("\n"):
            jl_old += "\n"
        jl_new = jl_old + json.dumps(entry, ensure_ascii=False) + "\n"

        # Build new MD content: existing + new H2 block
        md_old = md_path.read_text(encoding="utf-8") if md_path.exists() else f"# Journal for {parent.name}\n\n"
        if md_old and not md_old.endswith("\n"):
            md_old += "\n"
        md_new = md_old + _md_entry(entry)

        # Stage as siblings (same filesystem → atomic os.replace works)
        jl_tmp = jl_path.with_suffix(jl_path.suffix + ".tmp")
        md_tmp = md_path.with_suffix(md_path.suffix + ".tmp")
        jl_tmp.write_text(jl_new, encoding="utf-8")
        md_tmp.write_text(md_new, encoding="utf-8")

        # Rename JSONL first — it's canonical. Then MD.
        try:
            os.replace(jl_tmp, jl_path)
        except OSError as exc:
            logger.error("journal.append: failed to rename %s → %s: %s", jl_tmp, jl_path, exc)
            try: md_tmp.unlink()
            except OSError: pass
            return
        try:
            os.replace(md_tmp, md_path)
        except OSError as exc:
            logger.error(
                "journal.append: JSONL committed but MD rename failed (%s → %s): %s — JSONL is canonical",
                md_tmp, md_path, exc,
            )
        logger.info("journal.append: wrote entry to %s", parent)
