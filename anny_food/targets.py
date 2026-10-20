"""Load and query targets.toml (food-group targets by trimester)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TARGETS_PATH = Path(__file__).with_name("targets.toml")

TRIMESTERS = ("t1", "t2", "t3")
_PERIODS = {"day", "week"}
_KINDS = {"min", "limit"}


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    unit: str
    period: str                 # "day" | "week"
    kind: str                   # "min" | "limit"
    by_trimester: dict[str, float]
    derived_from: tuple[str, ...] = ()
    parent: str | None = None
    subset_of: str | None = None
    ceiling: float | None = None   # weekly hard max on a "min" row
    enabled: bool = True

    @property
    def derived(self) -> bool:
        return bool(self.derived_from)

    def daily(self, trimester: str) -> float | None:
        """Per-day figure, or None for week-period rows."""
        return self.by_trimester[trimester] if self.period == "day" else None

    def weekly(self, trimester: str) -> float:
        v = self.by_trimester[trimester]
        return v * 7 if self.period == "day" else v


@dataclass(frozen=True)
class Targets:
    rows: tuple[Target, ...]
    meta: dict
    safety_flags: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> Target:
        for t in self.rows:
            if t.key == key:
                return t
        raise KeyError(key)

    def enabled(self) -> list[Target]:
        return [t for t in self.rows if t.enabled]

    def value_keys(self) -> list[str]:
        """Keys a breakdown stores per serving: enabled, non-derived rows."""
        return [t.key for t in self.rows if t.enabled and not t.derived]


def _parse_target(raw: dict) -> Target:
    key = raw.get("key")
    if not key:
        raise ValueError(f"target without key: {raw!r}")
    if raw.get("period") not in _PERIODS:
        raise ValueError(f"{key}: period must be one of {sorted(_PERIODS)}")
    if raw.get("kind") not in _KINDS:
        raise ValueError(f"{key}: kind must be one of {sorted(_KINDS)}")
    missing = [t for t in TRIMESTERS if t not in raw]
    if missing:
        raise ValueError(f"{key}: missing {missing}")
    return Target(
        key=key,
        label=raw.get("label", key),
        unit=raw.get("unit", ""),
        period=raw["period"],
        kind=raw["kind"],
        by_trimester={t: float(raw[t]) for t in TRIMESTERS},
        derived_from=tuple(raw.get("derived_from", ())),
        parent=raw.get("parent"),
        subset_of=raw.get("subset_of"),
        ceiling=float(raw["ceiling"]) if "ceiling" in raw else None,
        enabled=bool(raw.get("enabled", True)),
    )


def load_targets(path: Path | str | None = None) -> Targets:
    p = Path(path) if path is not None else TARGETS_PATH
    with open(p, "rb") as f:
        data = tomllib.load(f)
    rows = tuple(_parse_target(r) for r in data.get("target", []))
    keys = [t.key for t in rows]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"duplicate target keys: {sorted(dupes)}")
    known = set(keys)
    for t in rows:
        refs = list(t.derived_from) + [r for r in (t.parent, t.subset_of) if r]
        unknown = [r for r in refs if r not in known]
        if unknown:
            raise ValueError(f"{t.key}: references unknown keys {unknown}")
    return Targets(
        rows=rows,
        meta=data.get("meta", {}),
        safety_flags=dict(data.get("safety_flags", {})),
    )
