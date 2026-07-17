"""Goals-page tests: pure-helper units (synthetic values only) plus render
smoke tests on the seeded and empty fixture DBs.

Imports of goals_view/cardiology_view/build_report happen INSIDE test bodies,
never at module level — their first import executes `import clinical_data as
CD`, which must only ever resolve to the fake module a fixture has already
installed into sys.modules (Standing rule 2: the real gitignored PHI module
never loads in a test process).
"""
import pandas as pd
import pytest
import streamlit as st


# --- pure helpers ------------------------------------------------------------

def _helpers():
    import dashboard.goals_view as gv
    return gv


TODAY = pd.Timestamp("2025-07-01")


def test_med_lanes_statin_segments_and_bounded_med(fake_clinical):
    gv = _helpers()
    lanes = gv._med_lanes(
        [("2025-01-01", 10, "start"), ("2025-03-01", 20, "up")],
        [{"name": "fakezet", "brand": "FakeZ", "dose": "0 mg",
          "start": "2025-02-01", "stop": "2025-05-01", "status": "discontinued"},
         {"name": "neverstarted", "start": None}],
        "fakestatin")
    statin = [s for s in lanes if s["lane"] == "fakestatin"]
    assert len(statin) == 2
    assert statin[0]["end"] == pd.Timestamp("2025-03-01")   # era ends at next step
    assert statin[1]["end"] is None                          # current era ongoing
    assert statin[1]["label"] == "20 mg"
    other = [s for s in lanes if s["lane"] == "FakeZ"]
    assert len(other) == 1 and other[0]["end"] == pd.Timestamp("2025-05-01")
    # med with no start date never becomes a lane
    assert not any(s["lane"] == "neverstarted" for s in lanes)


def test_med_lanes_excludes_statin_named_medication(fake_clinical):
    gv = _helpers()
    lanes = gv._med_lanes(
        [("2025-01-01", 10, "start")],
        [{"name": "fakestatin", "brand": "Fakezor", "dose": "10 mg",
          "start": "2025-01-01", "status": "active"}],
        "fakestatin")
    # the statin comes from the dose-event history, not doubled from MEDICATIONS
    assert len([s for s in lanes if "fakestatin" in s["lane"].lower()]) == 1


def test_projection_requires_active_started_pcsk9(fake_clinical):
    gv = _helpers()
    meds_yes = [{"name": "x", "purpose": "fixture (PCSK9 inhibitor)",
                 "start": "2025-06-01", "status": "active"}]
    lo, hi = gv._projection(100.0, meds_yes)
    assert lo == pytest.approx(100 * (1 - gv.PCSK9I_LDL_REDUCTION[1]))
    assert hi == pytest.approx(100 * (1 - gv.PCSK9I_LDL_REDUCTION[0]))
    assert gv._projection(100.0, [{**meds_yes[0], "status": "prescribed"}]) is None
    assert gv._projection(100.0, [{**meds_yes[0], "start": None}]) is None
    assert gv._projection(100.0, [{"name": "y", "purpose": "statin",
                                   "start": "2025-01-01", "status": "active"}]) is None
    assert gv._projection(None, meds_yes) is None


def test_next_injection_every_two_weeks(fake_clinical):
    gv = _helpers()
    meds = [{"name": "inj", "brand": "Inj", "frequency": "every 2 weeks",
             "start": "2025-06-01", "status": "active"}]
    name, nxt = gv._next_injection(meds, pd.Timestamp("2025-06-10"))
    assert name == "Inj" and nxt == pd.Timestamp("2025-06-15")
    # on the start day itself, the next dose is one full period out
    _, nxt = gv._next_injection(meds, pd.Timestamp("2025-06-01"))
    assert nxt == pd.Timestamp("2025-06-15")
    # dailies and not-yet-started meds contribute nothing
    assert gv._next_injection(
        [{"name": "d", "frequency": "daily", "start": "2025-01-01",
          "status": "active"}], TODAY) is None


def test_therapy_change_date_spans_events_and_stops(fake_clinical):
    gv = _helpers()
    change = gv._therapy_change_date(
        [("2025-01-01", 10, "start")],
        [{"name": "m", "start": "2025-02-01", "stop": "2025-08-01"}])
    assert change == pd.Timestamp("2025-08-01")
    assert gv._therapy_change_date([], []) is None


def test_pace_per_week(fake_clinical):
    gv = _helpers()
    pace = gv._pace_per_week(130, 120, TODAY, TODAY + pd.Timedelta(weeks=10))
    assert pace == pytest.approx(-1.0)
    assert gv._pace_per_week(115, 120, TODAY, TODAY + pd.Timedelta(weeks=10)) is None
    assert gv._pace_per_week(130, 120, TODAY, None) is None
    assert gv._pace_per_week(130, 120, TODAY, TODAY - pd.Timedelta(days=1)) is None


def test_spark_svg_guards(fake_clinical):
    gv = _helpers()
    assert gv._spark_svg([], "#fff") == ""
    assert gv._spark_svg([1.0], "#fff") == ""
    svg = gv._spark_svg([1.0, 2.0, 1.5], "#fff")
    assert svg.startswith("<svg") and "polyline" in svg


# --- render smoke ------------------------------------------------------------

def _render(db_path, clinical_mod, monkeypatch):
    import dashboard.goals_view as goals_view
    import dashboard.cardiology_view as cardiology_view
    from dashboard import lib

    import build_report

    # Same value-binding dance as test_cardiology_view: DB_PATH and CD are
    # captured at import time in each module, so rebind them all explicitly.
    monkeypatch.setattr(lib, "DB_PATH", db_path)
    monkeypatch.setattr(cardiology_view, "DB_PATH", db_path)
    monkeypatch.setattr(goals_view, "CD", clinical_mod)
    monkeypatch.setattr(cardiology_view, "CD", clinical_mod)
    monkeypatch.setattr(build_report, "CD", clinical_mod)
    st.cache_data.clear()
    goals_view.render_goals()


def test_render_goals_with_goals(fake_db, fake_clinical, monkeypatch):
    _render(fake_db, fake_clinical, monkeypatch)


def test_render_goals_without_goals(fake_db, fake_clinical_no_goals, monkeypatch):
    """Un-updated PHI file (no CARDIO_GOALS/MEDICATIONS/deadline): every
    section must degrade via its getattr guards, not crash."""
    _render(fake_db, fake_clinical_no_goals, monkeypatch)


def test_render_goals_on_empty_db(empty_db, fake_clinical, monkeypatch):
    """Day-one DB: no BP, no weight, no nutrition, no activities — every tile
    and section renders its empty state (the exercise tile takes the raw-
    duration fallback path via _frames' SystemExit)."""
    _render(empty_db, fake_clinical, monkeypatch)


def test_render_goals_on_empty_db_without_goals(empty_db, fake_clinical_no_goals,
                                                monkeypatch):
    _render(empty_db, fake_clinical_no_goals, monkeypatch)
