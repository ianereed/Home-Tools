"""Detect and mark cross-source duplicate activities.

The same real-world workout is recorded once on the device (a Garmin watch) and
then mirrored into aggregators that sync from it (Strava). Both land in the
`activities` table as separate rows — the `UNIQUE(source, source_id)` constraint
only stops same-source dupes, not cross-source ones — so every mirrored workout
is double-counted in totals, weekly load and TRIMP.

This module clusters the same workout across sources and marks the non-canonical
copies with `dup_of` pointing at the canonical row. Canonical = the device that
recorded it (see SOURCE_PRIORITY). Readers exclude `dup_of IS NOT NULL` rows from
counts/sums; the raw duplicate rows are kept (Strava carries the HR stream the
Garmin copy lacks) and resolved through the dup group when needed.

Run after every collection (collect_all calls dedupe_activities) and it is
idempotent — it recomputes the whole picture from scratch each time, so a copy
that later turns out not to be a dup is automatically un-marked.
"""

import logging
from datetime import datetime

from .db import get_connection

logger = logging.getLogger(__name__)

# Lower number = more authoritative = "the device that recorded it". A workout
# present in both Garmin and Strava was recorded on the Garmin watch and synced
# up to Strava (Strava never pushes back to Garmin), so Garmin always wins.
# Unlisted sources fall to the bottom and only ever win against each other.
SOURCE_PRIORITY = {"garmin": 0, "strava": 1}
_UNKNOWN_PRIORITY = 99

# Match tolerances. Distance, when both copies have a real one, is decisive:
# same-day pairs agree to ~0.1 km. Duration is the fallback for distance-less
# activities (strength, indoor) and is looser because Strava counts elapsed time
# while Garmin counts moving time (observed 0–3 min apart). start_time, when both
# have it, gates everything so two genuinely separate same-day workouts of
# similar length don't collapse together.
DISTANCE_ABS_KM = 0.3
DISTANCE_REL = 0.05
DURATION_ABS_MIN = 5.0
DURATION_REL = 0.15
START_TIME_MIN = 20.0


def _parse_start(value):
    """Parse a stored start_time into a datetime, or None if unusable."""
    if not value:
        return None
    try:
        # Garmin: "2026-06-05 07:14:32"; Strava: ISO "2026-06-05T07:14:32".
        return datetime.fromisoformat(str(value).replace("T", " ").split("+")[0].strip())
    except ValueError:
        return None


def _same_workout(a, b) -> bool:
    """True if rows a and b look like the same workout from different sources."""
    if a["date"] != b["date"]:
        return False

    sa, sb = _parse_start(a["start_time"]), _parse_start(b["start_time"])
    times_present = sa is not None and sb is not None
    if times_present and abs((sa - sb).total_seconds()) / 60.0 > START_TIME_MIN:
        return False

    da, db = a["distance_km"] or 0, b["distance_km"] or 0
    if da > 0.1 and db > 0.1:
        # Matching distance on the same day is a strong, near-unique signal.
        return abs(da - db) <= max(DISTANCE_ABS_KM, DISTANCE_REL * max(da, db))

    # No usable distance (strength, indoor, etc.): duration alone is too weak to
    # tell two same-day sessions apart, so only merge when BOTH rows carry a
    # start_time that already passed the proximity gate above. Without that,
    # we'd risk silently dropping a genuinely distinct same-day workout.
    if not times_present:
        return False
    ma, mb = a["duration_minutes"] or 0, b["duration_minutes"] or 0
    if ma > 0 and mb > 0:
        return abs(ma - mb) <= max(DURATION_ABS_MIN, DURATION_REL * max(ma, mb))

    return False


def _canonical(cluster):
    """The row to keep as authoritative: most-authoritative source, oldest id."""
    return min(
        cluster,
        key=lambda r: (SOURCE_PRIORITY.get(r["source"], _UNKNOWN_PRIORITY), r["id"]),
    )


def dedupe_activities(conn=None) -> dict:
    """Recompute `dup_of` across all activities. Returns a summary dict."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    try:
        rows = conn.execute(
            """SELECT id, date, source, source_id, start_time,
                      duration_minutes, distance_km
               FROM activities ORDER BY date, id"""
        ).fetchall()

        # Start clean so a row that is no longer a duplicate gets un-marked.
        conn.execute("UPDATE activities SET dup_of = NULL")

        by_date: dict[str, list] = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)

        groups = 0
        marked = 0
        for acts in by_date.values():
            clusters: list[list] = []
            for a in acts:
                for cl in clusters:
                    if any(_same_workout(a, b) for b in cl):
                        cl.append(a)
                        break
                else:
                    clusters.append([a])

            for cl in clusters:
                # Only cross-source clusters are duplicates; two same-source rows
                # on one day are distinct workouts the UNIQUE constraint allowed.
                if len(cl) < 2 or len({r["source"] for r in cl}) < 2:
                    continue
                groups += 1
                keep = _canonical(cl)
                for r in cl:
                    if r["id"] != keep["id"]:
                        conn.execute(
                            "UPDATE activities SET dup_of = ? WHERE id = ?",
                            (keep["id"], r["id"]),
                        )
                        marked += 1

        conn.commit()
        logger.info(
            "Activity de-dup: %d duplicate(s) across %d cross-source group(s) marked",
            marked, groups,
        )
        return {"groups": groups, "marked": marked, "total": len(rows)}
    finally:
        if own_conn:
            conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from .db import init_db
    init_db()
    print(dedupe_activities())
