"""Every tab renders on seeded data, and old links land on the tab that took
over their content."""
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest


@pytest.fixture
def app(fake_db, fake_clinical, monkeypatch, tmp_path):
    from dashboard import lib
    monkeypatch.setattr(lib, "_state_path", lambda: str(tmp_path / "state.json"))
    st.cache_data.clear()

    def run(**params):
        at = AppTest.from_file("dashboard/app.py", default_timeout=90)
        for k, v in params.items():
            at.query_params[k] = v
        at.run()
        assert not at.exception, at.exception
        return at
    return run


def _pages(at):
    return at.session_state["nav"], list(at.segmented_control(key="nav").options)


@pytest.mark.parametrize("slug", ["overview", "recovery", "training", "goals", "cardiology", "nutrition"])
def test_every_tab_renders(app, slug):
    at = app(page=slug)
    page, pages = _pages(at)
    if slug in ("goals", "cardiology") and "Goals" not in pages:
        pytest.skip("no clinical_data.py on this host: PHI tabs are hidden")
    assert page.lower() == slug
    assert not at.error, [e.value for e in at.error]


@pytest.mark.parametrize("params,expected", [
    ({"page": "fuel"}, "Nutrition"), ({"page": "diet"}, "Nutrition"),
    ({"page": "sleep"}, "Recovery"), ({"page": "fitness"}, "Training"),
    ({"p": "2"}, "Recovery"), ({"p": "4"}, "Training"), ({"p": "5"}, "Training"),
])
def test_old_links_redirect(app, params, expected):
    at = app(**params)
    assert at.session_state["nav"] == expected
    assert at.query_params["page"] == [expected.lower()]


def test_six_tabs_or_four_without_phi(app):
    _, pages = _pages(app())
    assert pages in (["Overview", "Recovery", "Training", "Goals", "Cardiology", "Nutrition"],
                     ["Overview", "Recovery", "Training", "Nutrition"])
