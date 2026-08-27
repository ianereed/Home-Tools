"""Phase 1 smoke tests: render_cardiology() must not raise, both with
CARDIO_GOALS/MEDICATIONS present (fake_clinical) and absent
(fake_clinical_no_goals) — the getattr-guarded backward-compatible path
required by Standing rule 2 / the Phase 1 spec.

Imports of cardiology_view/build_report happen INSIDE _render, not at module
level, because their first-ever import executes `import clinical_data as CD`
— that must run only after a fixture has already installed the fake module
into sys.modules['clinical_data'], never at test-collection time (which would
load the real gitignored PHI module, violating Standing rule 2).
"""
import streamlit as st


def _render(fake_db, clinical_mod, monkeypatch):
    import dashboard.cardiology_view as cardiology_view
    from dashboard import lib

    import build_report

    # cardiology_view.py and lib.py each capture DB_PATH via
    # `from collectors.db import DB_PATH` at import time — a value binding,
    # not a module reference — so monkeypatching collectors.db.DB_PATH (what
    # the fake_db fixture does) doesn't reach either module's own name.
    monkeypatch.setattr(cardiology_view, "DB_PATH", fake_db)
    monkeypatch.setattr(lib, "DB_PATH", fake_db)
    # Same value-binding issue for `import clinical_data as CD`: rebind both
    # modules' own CD name to this test's fixture module explicitly, rather
    # than relying on import-time binding (which may be stale if either
    # module was already imported by an earlier test).
    monkeypatch.setattr(cardiology_view, "CD", clinical_mod)
    monkeypatch.setattr(build_report, "CD", clinical_mod)
    # st.cache_data is a process-global cache keyed on function args (not on
    # DB_PATH), so a stale entry from a previous test/DB would leak through.
    st.cache_data.clear()
    cardiology_view.render_cardiology()


def test_render_cardiology_with_goals(fake_db, fake_clinical, monkeypatch):
    _render(fake_db, fake_clinical, monkeypatch)


def test_render_cardiology_without_goals(fake_db, fake_clinical_no_goals, monkeypatch):
    _render(fake_db, fake_clinical_no_goals, monkeypatch)


def test_render_cardiology_on_empty_db(empty_db, fake_clinical, monkeypatch):
    """Phase 3: a freshly-initialized DB with zero rows anywhere (the day-one,
    no-scale/no-BP-cuff/no-wearable-history reality) must render every new BP/
    weight/body-composition section via its empty-state path, not crash."""
    _render(empty_db, fake_clinical, monkeypatch)


def test_render_cardiology_on_empty_db_without_goals(empty_db, fake_clinical_no_goals, monkeypatch):
    _render(empty_db, fake_clinical_no_goals, monkeypatch)


def test_bp_daypart_stats_split_and_window(fake_clinical):
    """AM/PM split at noon, means per cohort, gap = AM minus PM, and readings
    older than the window are excluded."""
    import pandas as pd

    import dashboard.cardiology_view as cardiology_view

    bp = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2025-06-01 07:00", "2025-06-02 08:30",   # AM cohort
            "2025-06-01 20:00", "2025-06-02 12:00",   # PM cohort (noon counts)
            "2024-01-01 07:00",                        # outside 90-day window
        ]),
        "systolic": [140, 150, 120, 130, 199],
        "diastolic": [90, 100, 80, 90, 99],
    })
    s = cardiology_view._bp_daypart_stats(
        bp, window_days=90, now=pd.Timestamp("2025-06-30 12:00"))
    assert s["n"] == 4
    assert s["am"] == {"n": 2, "sys": 145, "dia": 95}
    assert s["pm"] == {"n": 2, "sys": 125, "dia": 85}
    assert s["gap"] == (20, 10)


def test_bp_daypart_stats_one_sided(fake_clinical):
    """A window with only-morning readings yields pm=None and gap=None (the
    synthetic fixture DB is all-08:00, so the render path hits this too)."""
    import pandas as pd

    import dashboard.cardiology_view as cardiology_view

    bp = pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-06-01 07:00", "2025-06-02 08:00"]),
        "systolic": [140, 150], "diastolic": [90, 100],
    })
    s = cardiology_view._bp_daypart_stats(
        bp, window_days=90, now=pd.Timestamp("2025-06-30 12:00"))
    assert s["am"]["n"] == 2 and s["pm"] is None and s["gap"] is None


def test_bp_med_starts_filters_on_purpose(fake_clinical, monkeypatch):
    """Only medications whose purpose says blood pressure produce markers;
    the fixture's lipid meds (purpose 'fixture only') must not."""
    import dashboard.cardiology_view as cardiology_view

    monkeypatch.setattr(cardiology_view, "CD", fake_clinical)
    fake_clinical.MEDICATIONS = fake_clinical.MEDICATIONS + [
        {"name": "fakesartan", "brand": "Fakenicar", "dose": "0 mg",
         "form": "oral tablet", "frequency": "daily at bedtime",
         "start": "2025-05-01", "status": "active",
         "prescriber": "Dr. Fake", "purpose": "blood-pressure lowering (ARB)",
         "note": "synthetic fixture entry"},
    ]
    starts = cardiology_view._bp_med_starts()
    assert [(str(d.date()), n) for d, n in starts] == [("2025-05-01", "Fakenicar")]


def test_medications_html_hides_discontinued(fake_clinical, monkeypatch):
    """The Cardiology "Medications" header shows the current regimen only:
    a discontinued med (has "stop" / "discontinued …" status) stays in
    MEDICATIONS for the Goals-tab regimen lanes but must not render a card;
    active and prescribed-not-yet-started meds still do."""
    import dashboard.cardiology_view as cardiology_view

    monkeypatch.setattr(cardiology_view, "CD", fake_clinical)
    html = cardiology_view._medications_html()
    assert "Fakezor" in html
    assert "Fakepha" in html
    assert "Fakezetia" not in html


def test_stat_cards_apob_none_on_latest_and_nadir(fake_clinical, monkeypatch):
    """A lipid-only draw (LDL present, ApoB not ordered) can be BOTH the latest
    row and the LDL nadir. stat_cards_html must render an em-dash for the
    missing ApoB instead of crashing on int(NaN)."""
    import build_report

    monkeypatch.setattr(build_report, "CD", fake_clinical)
    fake_clinical.LIPID_PANELS = fake_clinical.LIPID_PANELS + [
        ("2025-09-15", 20, None, 90, 100, 45, 40, None, None, "fake lipid-only draw"),
    ]
    html = build_report.stat_cards_html(build_report.lipids_df())
    assert "—" in html
    assert "40" in html
