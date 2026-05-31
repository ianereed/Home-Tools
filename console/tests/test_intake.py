"""Unit tests for console/tabs/intake.py discovery + breadcrumb logic.

`render()` (Streamlit) is not exercised — same convention as the other tabs.
We test the pure helpers against a tmp_path fake NAS.
"""
from __future__ import annotations

from pathlib import Path

import console.tabs.intake as intake_mod
from console.tabs.intake import (
    _cache_age_seconds,
    _load_cache,
    _recipe_photo_dir,
    _scan,
    _slug,
    _write_cache,
    breadcrumb,
    find_intakes,
)


def _mkdirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def test_finds_nested_intakes_case_insensitive(tmp_path: Path) -> None:
    _mkdirs(
        tmp_path / "Financial" / "intake",
        tmp_path / "Healthcare" / "0-Ian Healthcare" / "Intake",
    )
    found = {p.relative_to(tmp_path).as_posix() for p in find_intakes(tmp_path)}
    assert found == {"Financial/intake", "Healthcare/0-Ian Healthcare/Intake"}


def test_does_not_descend_into_matched_intake(tmp_path: Path) -> None:
    # An intake/ nested inside another intake/ must not be reported separately.
    _mkdirs(tmp_path / "Docs" / "intake" / "intake")
    found = [p.relative_to(tmp_path).as_posix() for p in find_intakes(tmp_path)]
    assert found == ["Docs/intake"]


def test_respects_depth_cap(tmp_path: Path) -> None:
    # intake at depth 5 (a/b/c/d/intake) is beyond INTAKE_DEPTH_MAX=4.
    deep = tmp_path / "a" / "b" / "c" / "d" / "intake"
    shallow = tmp_path / "a" / "intake"
    _mkdirs(deep, shallow)
    found = {p.relative_to(tmp_path).as_posix() for p in find_intakes(tmp_path, max_depth=4)}
    assert "a/intake" in found
    assert "a/b/c/d/intake" not in found


def test_skips_skip_dirs_and_dotfiles(tmp_path: Path) -> None:
    _mkdirs(
        tmp_path / "#recycle" / "intake",
        tmp_path / "@eaDir" / "intake",
        tmp_path / ".hidden" / "intake",
        tmp_path / "_processed" / "intake",
        tmp_path / "Real" / "intake",
    )
    found = {p.relative_to(tmp_path).as_posix() for p in find_intakes(tmp_path)}
    assert found == {"Real/intake"}


def test_empty_or_missing_root(tmp_path: Path) -> None:
    assert find_intakes(tmp_path) == []
    assert find_intakes(tmp_path / "nope") == []


def test_breadcrumb(tmp_path: Path) -> None:
    root = tmp_path / "Share1"
    intake = root / "Healthcare" / "0-Ian Healthcare" / "Intake"
    _mkdirs(intake)
    assert breadcrumb(intake, root) == "Share1 > Healthcare > 0-Ian Healthcare > Intake"


def test_slug_is_stable_and_filesystem_safe(tmp_path: Path) -> None:
    root = tmp_path / "Share1"
    intake = root / "Healthcare" / "0-Ian Healthcare" / "Intake"
    _mkdirs(intake)
    slug = _slug(intake, root)
    assert slug == "healthcare_0_ian_healthcare_intake"
    # distinct folders produce distinct slugs
    other = root / "Financial" / "intake"
    _mkdirs(other)
    assert _slug(other, root) != slug


def test_recipe_photo_dir_default_is_nas_relative(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MEAL_PLANNER_NAS_INTAKE_DIR", raising=False)
    root = tmp_path / "Share1"
    assert _recipe_photo_dir(root) == root / "Documents" / "Recipes" / "photo-intake"


def test_recipe_photo_dir_honors_env_override(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "elsewhere" / "photo-intake"
    monkeypatch.setenv("MEAL_PLANNER_NAS_INTAKE_DIR", str(override))
    # env wins over the NAS-relative default
    assert _recipe_photo_dir(tmp_path / "Share1") == override


def test_recipe_photo_dir_is_not_matched_by_find_intakes(tmp_path: Path) -> None:
    # photo-intake isn't named `intake`, so generic discovery must ignore it;
    # it's added separately as a special destination.
    _mkdirs(tmp_path / "Documents" / "Recipes" / "photo-intake")
    assert find_intakes(tmp_path) == []


# ── persistent cache + background scan ───────────────────────────────────────


def test_cache_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(intake_mod, "_CACHE_FILE", tmp_path / "intake_cache.json")
    assert _load_cache() is None  # nothing written yet
    _write_cache("/nas", ["/nas/a/intake"], "/nas/Documents/Recipes/photo-intake")
    cache = _load_cache()
    assert cache["root"] == "/nas"
    assert cache["folders"] == ["/nas/a/intake"]
    assert cache["recipe_dir"] == "/nas/Documents/Recipes/photo-intake"
    assert "scanned_at" in cache


def test_load_cache_tolerates_corrupt_file(tmp_path: Path, monkeypatch) -> None:
    cache_file = tmp_path / "intake_cache.json"
    cache_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(intake_mod, "_CACHE_FILE", cache_file)
    assert _load_cache() is None


def test_cache_age_seconds_unparseable_is_inf() -> None:
    assert _cache_age_seconds({}) == float("inf")
    assert _cache_age_seconds({"scanned_at": "nonsense"}) == float("inf")


def test_scan_writes_discovered_folders(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(intake_mod, "_CACHE_FILE", tmp_path / "intake_cache.json")
    monkeypatch.delenv("MEAL_PLANNER_NAS_INTAKE_DIR", raising=False)
    _mkdirs(
        tmp_path / "Financial" / "intake",
        tmp_path / "Documents" / "Recipes" / "photo-intake",
    )
    _scan(str(tmp_path))
    cache = _load_cache()
    assert cache["folders"] == [str(tmp_path / "Financial" / "intake")]
    assert cache["recipe_dir"] == str(tmp_path / "Documents" / "Recipes" / "photo-intake")
    assert intake_mod._SCAN_STATE["error"] is None
    assert intake_mod._SCAN_STATE["count"] == 1


def test_scan_unions_new_with_known_and_prunes_vanished(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(intake_mod, "_CACHE_FILE", tmp_path / "intake_cache.json")
    # Prior cache lists two folders; only one still exists on disk, plus a brand
    # new one the walk will discover.
    real_old = tmp_path / "Real" / "intake"
    brand_new = tmp_path / "Fresh" / "intake"
    _mkdirs(real_old, brand_new)
    _write_cache(
        str(tmp_path),
        [str(real_old), str(tmp_path / "Gone" / "intake")],  # second no longer exists
        None,
    )
    _scan(str(tmp_path))
    folders = set(_load_cache()["folders"])
    assert str(real_old) in folders       # survived (still on disk)
    assert str(brand_new) in folders      # added by the walk
    assert str(tmp_path / "Gone" / "intake") not in folders  # pruned (vanished)


def test_scan_records_error_when_root_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(intake_mod, "_CACHE_FILE", tmp_path / "intake_cache.json")
    _scan(str(tmp_path / "does-not-exist"))
    assert intake_mod._SCAN_STATE["error"] is not None
    assert _load_cache() is None  # failure must not write/blank the cache
