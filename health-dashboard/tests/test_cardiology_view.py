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
