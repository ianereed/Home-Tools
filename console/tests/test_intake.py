"""Unit tests for console/tabs/intake.py discovery + breadcrumb logic.

`render()` (Streamlit) is not exercised — same convention as the other tabs.
We test the pure helpers against a tmp_path fake NAS.
"""
from __future__ import annotations

from pathlib import Path

from console.tabs.intake import breadcrumb, find_intakes, _slug


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
