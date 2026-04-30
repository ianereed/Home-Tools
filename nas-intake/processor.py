"""Per-file pipeline: subprocess to event-aggregator, parent-rooted filing, archive source, journal append.

Two paths share most code:

  **Small-file path** (default): subprocess `main.py ingest-image --file <path>`
  with NAS_WRITE_DISABLED=1, bounded by SUBPROCESS_TIMEOUT_S. On timeout we
  bump state.timeout_counts[sha].

  **Large-file path** (escalation): once a file has hit
  LARGE_FILE_TRIGGER_TIMEOUTS, we switch to `main.py ingest-image-large` —
  page-resumable, no internal timeout. The parent (this module) runs a
  watchdog that polls the heartbeat file the child writes; if the heartbeat
  is stale beyond LARGE_FILE_HEARTBEAT_STALE_S, we kill the subprocess and
  mark the file wedged.

Wedged files: source is renamed `_WEDGED_<orig>` IN PLACE (no movement to
subfolders, per the user's chosen UX). A `_WEDGED_<orig>.diagnostic.log` is
written next to it summarizing the last heartbeat and reason.

For each candidate file in an intake/ folder:
  1. SHA256 dedup check (in watcher)
  2. Small or large subprocess depending on state.timeout_counts[sha]
  3. Read _metadata.json from event-aggregator/staging/local_<sha>/
  4. Build parent-rooted destination: <parent>/<year>/<doc-type>/<date>_<slug>[-N]/
  5. Copy staged dir contents (page renderings + extraction artifacts) + the original
  6. Move source: intake/<file> → intake/_processed/<YYYY-MM>/<file>
  7. Purge event-aggregator's staging dir
  8. Append journal entry on parent
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)


# ── doc-type → folder name (verbatim from event-aggregator/writers/file_writer.py) ──

_DOC_TYPE_FOLDER = {
    "medical_portal_screenshot": "Portal-Screenshots",
    "medical_form": "Forms",
    "insurance_eob": "Insurance-EOB",
    "insurance_document": "Insurance",
    "prescription": "Prescriptions",
    "lab_results": "Lab-Results",
    "receipt": "Receipts",
    "invoice": "Invoices",
    "tax_form": "Tax-Documents",
    "bank_statement": "Bank-Statements",
    "contract": "Contracts",
    "id_card": "ID-Cards",
    "recipe": "Recipes",
    "photo": "Photos",
    "home_improvement": "Projects",
    "mortgage_document": "Mortgage",
    "utility_bill": "Utilities",
}


def _doc_type_to_folder(doc_type: str) -> str:
    if not doc_type:
        return "General"
    mapped = _DOC_TYPE_FOLDER.get(doc_type.lower().strip())
    if mapped:
        return mapped
    return "-".join(word.capitalize() for word in doc_type.replace("_", " ").split()) or "General"


def _slugify(text: str, max_len: int = 60) -> str:
    slug = (text or "").lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug).strip("-")
    return slug[:max_len] or "untitled"


# ── result type ─────────────────────────────────────────────────────────

@dataclass
class ProcessResult:
    ok: bool
    reason: str = ""
    filed_path: Path | None = None
    journal_entry: dict | None = None
    timed_out: bool = False  # small-file path only — set so caller can bump counter
    wedged: bool = False     # large-file path only — set so caller can mark wedged in state
    last_heartbeat: dict | None = None  # forensic context on wedge


# ── pipeline ────────────────────────────────────────────────────────────

def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_dest(parent: Path, meta: dict) -> Path:
    """parent / <year> / <doc-type-folder> / <date>_<slug>[-N]/"""
    date_str = (meta.get("date") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    year = date_str[:4]
    doc_type_folder = _doc_type_to_folder(meta.get("document_type", ""))
    slug = _slugify(meta.get("title") or "untitled")
    base = parent / year / doc_type_folder
    candidate = base / f"{date_str}_{slug}"
    if not candidate.exists():
        return candidate
    for i in range(2, 100):
        c = base / f"{date_str}_{slug}-{i}"
        if not c.exists():
            return c
    raise RuntimeError(f"too many path collisions under {base} for slug {slug}")


# ── small-file subprocess (existing path) ──────────────────────────────

def _run_ingest_image(file: Path) -> tuple[int, str, str]:
    """Subprocess to event-aggregator's small-file CLI with NAS_WRITE_DISABLED=1.
    Returns (returncode, stdout, stderr); rc=-1 means we hit the wall-clock timeout.
    """
    env = {**os.environ, "NAS_WRITE_DISABLED": "1"}
    cmd = [str(config.EA_VENV_PYTHON), "main.py", "ingest-image", "--file", str(file)]
    logger.info("processor: invoking %s (env NAS_WRITE_DISABLED=1)", " ".join(cmd))
    try:
        r = subprocess.run(
            cmd, cwd=str(config.EVENT_AGGREGATOR_ROOT),
            env=env, capture_output=True, text=True,
            timeout=config.SUBPROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return (-1, "", f"subprocess timed out after {config.SUBPROCESS_TIMEOUT_S}s")
    return (r.returncode, r.stdout or "", r.stderr or "")


# ── large-file subprocess + watchdog ───────────────────────────────────

def _read_heartbeat(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _run_ingest_image_large(file: Path, file_sha: str) -> tuple[int, str, str, dict | None, bool]:
    """Run the large-file pipeline as a subprocess with a heartbeat-driven
    watchdog. Returns (returncode, stdout, stderr, last_heartbeat, wedged).

    `wedged=True` means we killed the subprocess because no progress signal
    arrived for LARGE_FILE_HEARTBEAT_STALE_S. The (rc, stdout, stderr) on
    wedge will reflect the kill (non-zero rc, partial output).
    """
    env = {**os.environ, "NAS_WRITE_DISABLED": "1"}
    cmd = [str(config.EA_VENV_PYTHON), "main.py", "ingest-image-large", "--file", str(file)]
    staging_dir = (
        config.EVENT_AGGREGATOR_ROOT / "staging" / f"local_{file_sha[:12]}"
    )
    heartbeat_path = staging_dir / "heartbeat.json"
    logger.info(
        "processor: invoking large-file path %s (env NAS_WRITE_DISABLED=1, heartbeat=%s)",
        " ".join(cmd), heartbeat_path,
    )

    # Pre-clean stale heartbeat if a prior aborted run left one. This way the
    # "fresh ts ≠ start ts" check kicks in cleanly.
    try:
        heartbeat_path.unlink(missing_ok=True)
    except OSError:
        pass

    proc = subprocess.Popen(
        cmd, cwd=str(config.EVENT_AGGREGATOR_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    last_hb: dict | None = None
    last_hb_ts: str | None = None
    last_progress = time.monotonic()
    wedged = False

    poll_interval = max(5, config.LARGE_FILE_HEARTBEAT_POLL_S)
    stale_threshold = max(60, config.LARGE_FILE_HEARTBEAT_STALE_S)

    while proc.poll() is None:
        time.sleep(poll_interval)
        cur = _read_heartbeat(heartbeat_path)
        if cur is not None and cur.get("ts") != last_hb_ts:
            last_hb = cur
            last_hb_ts = cur.get("ts")
            last_progress = time.monotonic()
            logger.debug(
                "watchdog: heartbeat ok phase=%s page=%s/%s op=%s",
                cur.get("phase"), cur.get("page_done"), cur.get("page_total"),
                cur.get("current_op"),
            )
            continue
        idle = time.monotonic() - last_progress
        if idle > stale_threshold:
            logger.warning(
                "watchdog: no progress for %.0fs (threshold %ds) — killing subprocess",
                idle, stale_threshold,
            )
            wedged = True
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            break

    # If we killed it, .communicate() flushes any remaining piped output.
    try:
        stdout, stderr = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    return (rc, stdout or "", stderr or "", last_hb, wedged)


# ── staging + filing helpers (shared by both paths) ────────────────────

def _find_staged_dir(file_sha: str) -> Path | None:
    """ingest-image stages files at event-aggregator/staging/local_<sha12>/."""
    expected = config.EVENT_AGGREGATOR_ROOT / "staging" / f"local_{file_sha[:12]}"
    return expected if expected.exists() else None


def _copy_staged_to_dest(staged: Path, source: Path, dest: Path) -> None:
    """Copy contents of staged/ to dest/, plus the original source file.
    Skips _metadata.json (internal), heartbeat.json (forensic), and pages/
    (large-file scratch dir — already represented by the renamed page files
    that stage_document_locally writes at the staging root)."""
    dest.mkdir(parents=True, exist_ok=True)
    INTERNAL_NAMES = {"_metadata.json", "heartbeat.json", "heartbeat.json.tmp"}
    INTERNAL_DIRS = {"pages"}
    for item in staged.iterdir():
        if item.name in INTERNAL_NAMES:
            continue
        if item.is_dir() and item.name in INTERNAL_DIRS:
            continue
        if item.is_dir():
            shutil.copytree(str(item), str(dest / item.name), dirs_exist_ok=True)
        else:
            shutil.copy2(str(item), str(dest / item.name))
    shutil.copy2(str(source), str(dest / source.name))


def _archive_source(source: Path, intake_dir: Path) -> Path:
    """Move source → intake/_processed/YYYY-MM/<file>."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    archive = intake_dir / "_processed" / month
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / source.name
    if target.exists():
        for i in range(2, 100):
            c = archive / f"{source.stem}-{i}{source.suffix}"
            if not c.exists():
                target = c
                break
    shutil.move(str(source), str(target))
    return target


def _purge_staged(staged: Path) -> None:
    try:
        shutil.rmtree(staged)
    except OSError as exc:
        logger.warning("processor: failed to purge staging %s: %s", staged, exc)


def _wedge_in_place(source: Path, last_hb: dict | None, reason: str) -> Path:
    """Rename source → _WEDGED_<orig> and write a sibling diagnostic log.
    Returns the renamed path. Idempotent if already wedged.
    """
    if source.name.startswith("_WEDGED_"):
        return source
    target = source.with_name(f"_WEDGED_{source.name}")
    # Avoid clobbering an existing wedge artifact.
    if target.exists():
        for i in range(2, 100):
            c = source.with_name(f"_WEDGED_{i}_{source.name}")
            if not c.exists():
                target = c
                break
    try:
        shutil.move(str(source), str(target))
    except OSError as exc:
        logger.warning("processor: could not rename to wedge name (%s → %s): %s",
                       source, target, exc)
        return source

    # Sibling diagnostic log — same parent so user can find it next to the
    # wedged file in the Intake folder. Best-effort.
    try:
        diag_path = target.with_name(target.name + ".diagnostic.log")
        lines = [
            f"WEDGED at {datetime.now(timezone.utc).isoformat()}",
            f"original: {source.name}",
            f"reason: {reason}",
        ]
        if last_hb:
            lines.append(f"last heartbeat: {json.dumps(last_hb, indent=2)}")
        else:
            lines.append("last heartbeat: (none — child never wrote one)")
        lines.append("")
        lines.append(
            "See ~/Library/Logs/home-tools-nas-intake-large/<sha12>.log on the mini "
            "for the per-file diagnostic trace (page-by-page timings, Ollama state)."
        )
        diag_path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        logger.warning("processor: could not write diag sibling log: %s", exc)
    return target


# ── public entry point ────────────────────────────────────────────────

def process_one(
    file: Path, parent: Path, intake_dir: Path, file_sha: str, state=None,
) -> ProcessResult:
    """Run the full pipeline on one file. Returns ProcessResult.

    Caller is responsible for filtering (extension, dedup, stability gate)
    BEFORE calling — process_one assumes it should run.

    `state` is optional for backwards-compat; when provided, it controls the
    small-vs-large path decision via `state.should_use_large_file_path(sha)`.
    """
    use_large = bool(state and state.should_use_large_file_path(file_sha))
    if use_large:
        logger.info(
            "processor: %s — large-file path (timeout_counts[%s]=%d)",
            file.name, file_sha[:12],
            state.timeout_counts.get(file_sha, 0),
        )
        return _process_one_large(file, parent, intake_dir, file_sha, state)
    logger.info("processor: processing %s under parent %s", file.name, parent)
    return _process_one_small(file, parent, intake_dir, file_sha, state)


def _process_one_small(
    file: Path, parent: Path, intake_dir: Path, file_sha: str, state,
) -> ProcessResult:
    rc, stdout, stderr = _run_ingest_image(file)
    if rc == -1 and "timed out" in stderr:
        return ProcessResult(False, stderr.strip(), timed_out=True)
    if rc != 0:
        return ProcessResult(False, f"ingest-image rc={rc}; stderr={stderr.strip()[:300]}")
    return _finish_filing(file, parent, intake_dir, file_sha)


def _process_one_large(
    file: Path, parent: Path, intake_dir: Path, file_sha: str, state,
) -> ProcessResult:
    staging_id = f"local_{file_sha[:12]}"
    if state is not None:
        state.mark_in_flight_large(file_sha, file, staging_id)
        state.save()

    rc, stdout, stderr, last_hb, wedged = _run_ingest_image_large(file, file_sha)
    if wedged:
        reason = (
            f"heartbeat stale > {config.LARGE_FILE_HEARTBEAT_STALE_S}s; "
            f"last phase={last_hb.get('phase') if last_hb else '(none)'} "
            f"page_done={last_hb.get('page_done') if last_hb else '?'}"
        )
        return ProcessResult(False, reason, wedged=True, last_heartbeat=last_hb)
    if rc != 0:
        return ProcessResult(
            False,
            f"ingest-image-large rc={rc}; stderr={stderr.strip()[:300]}",
            last_heartbeat=last_hb,
        )

    return _finish_filing(file, parent, intake_dir, file_sha, last_hb=last_hb)


def _finish_filing(
    file: Path, parent: Path, intake_dir: Path, file_sha: str,
    last_hb: dict | None = None,
) -> ProcessResult:
    """Locate the staged dir, build dest, copy, archive source, purge staging,
    return a ProcessResult ready for the journal. Shared by both paths.
    """
    staged = _find_staged_dir(file_sha)
    if staged is None:
        return ProcessResult(False, f"staged dir not found at staging/local_{file_sha[:12]}",
                             last_heartbeat=last_hb)

    meta_path = staged / "_metadata.json"
    if not meta_path.exists():
        return ProcessResult(False, f"_metadata.json missing in {staged}",
                             last_heartbeat=last_hb)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return ProcessResult(False, f"bad metadata: {exc}", last_heartbeat=last_hb)

    try:
        dest = _build_dest(parent, meta)
    except Exception as exc:
        return ProcessResult(False, f"build_dest failed: {exc}", last_heartbeat=last_hb)

    try:
        _copy_staged_to_dest(staged, file, dest)
    except Exception as exc:
        return ProcessResult(False, f"copy to NAS failed: {exc}", last_heartbeat=last_hb)

    try:
        archived = _archive_source(file, intake_dir)
    except Exception as exc:
        return ProcessResult(
            False, f"archive source failed (file copied to NAS at {dest}): {exc}",
            last_heartbeat=last_hb,
        )

    _purge_staged(staged)

    rel = dest.relative_to(parent) if dest.is_relative_to(parent) else dest
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "filed_path": str(dest),
        "filed_rel": str(rel),
        "source_name": file.name,
        "title": meta.get("title", ""),
        "doc_date": meta.get("date", ""),
        "doc_type": meta.get("document_type", ""),
        "category": meta.get("primary_category", ""),
        "subcategory": meta.get("subcategory", ""),
        "confidence": meta.get("confidence", 0.0) if isinstance(meta.get("confidence"), (int, float)) else 0.0,
        "summary": meta.get("summary", ""),
        "sha256": file_sha,
        "archived_to": str(archived),
    }
    return ProcessResult(True, "", filed_path=dest, journal_entry=entry, last_heartbeat=last_hb)
