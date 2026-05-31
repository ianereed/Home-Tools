"""Phase 16 Chunk 2 — tests for meal_planner_photo_intake_scan kind."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import jobs.kinds.meal_planner_photo_intake_scan as scan_mod
from meal_planner.db import _SCHEMA, _get_conn


def _fake_jpg(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def _setup(tmp_path: Path, monkeypatch, enqueue_mock: MagicMock):
    """Wire up a tmp intake_dir + tmp DB and return (intake_dir, db_path)."""
    import jobs.lib
    import meal_planner.vision.intake_db as idb

    intake_dir = tmp_path / "photo-intake"
    intake_dir.mkdir()
    db_p = tmp_path / "recipes.db"
    with _get_conn(db_p) as c:
        c.executescript(_SCHEMA)

    monkeypatch.setattr(idb, "DB_PATH", db_p)
    monkeypatch.setenv("MEAL_PLANNER_NAS_INTAKE_DIR", str(intake_dir))
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])
    # Prevent the real ingest task from running during scan tests.
    monkeypatch.setattr(scan_mod, "meal_planner_ingest_photo", enqueue_mock)
    return intake_dir, db_p


def test_scan_discovers_and_enqueues(tmp_path, monkeypatch):
    """Two JPGs in drop zone → both renamed to _processing/, both DB rows, both enqueues."""
    enqueue_mock = MagicMock()
    intake_dir, db_p = _setup(tmp_path, monkeypatch, enqueue_mock)

    _fake_jpg(intake_dir / "IMG_001.jpg", b"\xff\xd8\xff\xe0" + b"\x01" * 100)
    _fake_jpg(intake_dir / "IMG_002.jpg", b"\xff\xd8\xff\xe0" + b"\x02" * 100)

    result = scan_mod.meal_planner_photo_intake_scan.func()

    assert result["discovered"] == 2
    assert result["enqueued"] == 2
    assert result["skipped_dup"] == 0

    # Subfolders created
    for sub in ("_processing", "_done", "_skipped", "_wedged"):
        assert (intake_dir / sub).is_dir()

    # Source files gone from drop zone root
    assert not (intake_dir / "IMG_001.jpg").exists()
    assert not (intake_dir / "IMG_002.jpg").exists()

    # Files now in _processing/ with sha names
    processing = list((intake_dir / "_processing").iterdir())
    assert len(processing) == 2
    assert all(f.suffix == ".jpg" for f in processing)

    # DB rows exist at status=pending
    from meal_planner.vision.intake_db import list_pending
    pending = list_pending(db_path=db_p)
    assert len(pending) == 2

    # Enqueue called once per photo with the sha
    assert enqueue_mock.call_count == 2
    enqueued_shas = {call.args[0] for call in enqueue_mock.call_args_list}
    db_shas = {r.sha for r in pending}
    assert enqueued_shas == db_shas


def test_scan_dedup_no_op(tmp_path, monkeypatch):
    """Re-dropping content with the same SHA is a no-op on the second scan."""
    enqueue_mock = MagicMock()
    intake_dir, db_p = _setup(tmp_path, monkeypatch, enqueue_mock)

    # First scan
    _fake_jpg(intake_dir / "IMG_003.jpg", b"\xff\xd8\xff\xe0" + b"\x03" * 100)
    result1 = scan_mod.meal_planner_photo_intake_scan.func()
    assert result1["enqueued"] == 1

    # Simulate the consumer picking up the row (transitions it out of pending).
    from meal_planner.vision.intake_db import list_pending, mark_status
    rows = list_pending(db_path=db_p)
    assert len(rows) == 1
    mark_status(rows[0].sha, "extracting", db_path=db_p)

    # Drop same content again under a different filename
    _fake_jpg(intake_dir / "IMG_003_dup.jpg", b"\xff\xd8\xff\xe0" + b"\x03" * 100)
    result2 = scan_mod.meal_planner_photo_intake_scan.func()

    assert result2["discovered"] == 1
    assert result2["enqueued"] == 0
    assert result2["skipped_dup"] == 1
    assert enqueue_mock.call_count == 1  # only the original enqueue


def test_scan_os_error_exits_cleanly(tmp_path, monkeypatch):
    """OSError from iterdir (NAS unmounted) → returns discovered=0 without raising."""
    import jobs.lib
    import meal_planner.vision.intake_db as idb
    db_p = tmp_path / "recipes.db"
    with _get_conn(db_p) as c:
        c.executescript(_SCHEMA)

    monkeypatch.setattr(idb, "DB_PATH", db_p)
    monkeypatch.setenv("MEAL_PLANNER_NAS_INTAKE_DIR", str(tmp_path / "does_not_exist"))
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])
    monkeypatch.setattr(scan_mod, "meal_planner_ingest_photo", MagicMock())

    result = scan_mod.meal_planner_photo_intake_scan.func()
    assert result["discovered"] == 0
    assert result["enqueued"] == 0
    assert "tick_at" in result


def test_scan_ignores_non_image_files(tmp_path, monkeypatch):
    """Non-image files (txt, md) in drop zone are skipped; only JPG counts.
    Also verifies uppercase/mixed-case extensions are accepted (T9)."""
    enqueue_mock = MagicMock()
    intake_dir, db_p = _setup(tmp_path, monkeypatch, enqueue_mock)

    (intake_dir / "notes.txt").write_text("not an image")
    (intake_dir / "README.md").write_text("also not an image")
    _fake_jpg(intake_dir / "real.jpg", b"\xff\xd8\xff\xe0" + b"\x04" * 100)
    _fake_jpg(intake_dir / "upper.JPG", b"\xff\xd8\xff\xe0" + b"\x05" * 100)
    _fake_jpg(intake_dir / "mixed.JPg", b"\xff\xd8\xff\xe0" + b"\x06" * 100)

    result = scan_mod.meal_planner_photo_intake_scan.func()

    assert result["discovered"] == 3
    assert result["enqueued"] == 3
    assert enqueue_mock.call_count == 3


def test_scan_accepts_heic_and_pdf_preserving_suffix(tmp_path, monkeypatch):
    """HEIC and PDF are now accepted; _processing keeps the original extension
    so the ingest task knows whether to rasterize (.pdf) or open directly."""
    enqueue_mock = MagicMock()
    intake_dir, db_p = _setup(tmp_path, monkeypatch, enqueue_mock)

    _fake_jpg(intake_dir / "recipe.heic", b"\x00\x00\x00\x18ftypheic" + b"\x07" * 100)
    _fake_jpg(intake_dir / "print.pdf", b"%PDF-1.4" + b"\x08" * 100)

    result = scan_mod.meal_planner_photo_intake_scan.func()

    assert result["discovered"] == 2
    assert result["enqueued"] == 2

    suffixes = sorted(f.suffix for f in (intake_dir / "_processing").iterdir())
    assert suffixes == [".heic", ".pdf"]


def test_scan_re_enqueues_orphaned_pending(tmp_path, monkeypatch):
    """Self-heal: pending row with existing _processing file is re-enqueued each tick."""
    enqueue_mock = MagicMock()
    intake_dir, db_p = _setup(tmp_path, monkeypatch, enqueue_mock)

    # Pre-seed a pending row with its file already in _processing/.
    proc_dir = intake_dir / "_processing"
    proc_dir.mkdir(parents=True, exist_ok=True)
    orphan_sha = "deadbeef00000001"
    orphan_file = proc_dir / f"{orphan_sha}.jpg"
    orphan_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\xaa" * 50)

    from meal_planner.vision.intake_db import record_intake
    record_intake(orphan_sha, source_path="original.jpg", nas_path=str(orphan_file), path=db_p)

    # Drop zone is empty — no new files.
    result = scan_mod.meal_planner_photo_intake_scan.func()

    assert result["re_enqueued"] >= 1
    assert result["discovered"] == 0
    enqueue_mock.assert_called_once_with(orphan_sha)


def test_scan_rename_failure_rolls_back_row(tmp_path, monkeypatch):
    """If rename fails, the DB row is deleted (rolled back) and enqueue is not called."""
    enqueue_mock = MagicMock()
    intake_dir, db_p = _setup(tmp_path, monkeypatch, enqueue_mock)
    _fake_jpg(intake_dir / "IMG_004.jpg", b"\xff\xd8\xff\xe0" + b"\x10" * 100)

    monkeypatch.setattr(Path, "rename", lambda self, dst: (_ for _ in ()).throw(OSError("no space")))

    result = scan_mod.meal_planner_photo_intake_scan.func()

    assert result["discovered"] == 1
    assert result["enqueued"] == 0

    from meal_planner.vision.intake_db import list_pending
    assert list_pending(db_path=db_p) == []
    enqueue_mock.assert_not_called()


def test_scan_enqueue_failure_marks_error(tmp_path, monkeypatch):
    """If enqueue raises, the file stays in _processing/ and row is marked ollama_error."""
    intake_dir = tmp_path / "photo-intake"
    intake_dir.mkdir()
    db_p = tmp_path / "recipes.db"

    import meal_planner.vision.intake_db as idb
    import jobs.lib
    from meal_planner.db import _SCHEMA, _get_conn
    with _get_conn(db_p) as c:
        c.executescript(_SCHEMA)

    monkeypatch.setattr(idb, "DB_PATH", db_p)
    monkeypatch.setenv("MEAL_PLANNER_NAS_INTAKE_DIR", str(intake_dir))
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])

    failing_enqueue = MagicMock(side_effect=RuntimeError("queue full"))
    monkeypatch.setattr(scan_mod, "meal_planner_ingest_photo", failing_enqueue)

    _fake_jpg(intake_dir / "IMG_005.jpg", b"\xff\xd8\xff\xe0" + b"\x20" * 100)

    result = scan_mod.meal_planner_photo_intake_scan.func()

    assert result["discovered"] == 1
    assert result["enqueued"] == 0

    # File moved to _processing/.
    processing = list((intake_dir / "_processing").iterdir())
    assert len(processing) == 1

    # Row exists at ollama_error.
    from meal_planner.vision.intake_db import list_pending, get_by_sha
    pending = list_pending(db_path=db_p)
    assert len(pending) == 0
    all_rows = idb._get_conn(db_p).execute("SELECT sha, status FROM photos_intake").fetchall()
    assert len(all_rows) == 1
    assert all_rows[0][1] == "ollama_error"
