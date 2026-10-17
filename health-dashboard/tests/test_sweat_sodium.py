"""Ride sweat sodium -> negative-sodium MFP entry: estimator, idempotent ledger, MFP op runner, fetch split."""
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from collectors import mfp_collector as M
from collectors import mfp_fetch as F
from collectors import mfp_sweat as S
from nutrition import sweat
from nutrition.settings import DEFAULTS

TODAY = date(2026, 10, 16)


def _cfg(**kw):
    return {**DEFAULTS["sweat_sodium"], "mg_per_l": 1000, **kw}


def _settings(**kw):
    return {"sweat_sodium": _cfg(**kw)}


# ------------------------------------------------------------------ estimator
def test_estimate_uses_garmin_water():
    e = sweat.estimate({"id": 1, "duration_s": 3600}, 569.0, _cfg(mg_per_l=900))
    assert e == {"water_ml": 569, "water_source": "garmin", "mg_per_l": 900, "sodium_mg": 512, "override": False}


def test_estimate_falls_back_to_duration_when_garmin_has_no_water():
    e = sweat.estimate({"id": 1, "duration_s": 5400}, None, _cfg(fallback_sweat_ml_per_h=600))
    assert e["water_source"] == "fallback" and e["water_ml"] == 900 and e["sodium_mg"] == 900


def test_estimate_without_concentration_logs_nothing():
    assert sweat.estimate({"id": 1, "duration_s": 3600}, 800.0, _cfg(mg_per_l=None))["sodium_mg"] is None


def test_estimate_override_and_min_mg():
    assert sweat.estimate({"id": 7, "duration_s": 3600}, 800.0, _cfg(override_mg={"7": 1234}))["sodium_mg"] == 1234
    assert sweat.estimate({"id": 1, "duration_s": 600}, 40.0, _cfg(min_mg=50))["sodium_mg"] == 0


def test_water_from_detail():
    assert sweat.water_from_detail({"summaryDTO": {"waterEstimated": 1322.0}}) == 1322.0
    assert sweat.water_from_detail({"summaryDTO": {}}) is None
    assert sweat.water_from_detail(None) is None


# ------------------------------------------------------------------ sync / ledger
def _add_act(conn, aid, ds, typ="road_biking", dur=3600, dup_of=None):
    conn.execute("INSERT INTO activities (date, type, source, source_id, start_time, duration_s, name, dup_of) "
                 "VALUES (?,?,?,?,?,?,?,?)", (ds, typ, "garmin", aid, f"{ds} 10:00:00", dur, "Ride", dup_of))
    conn.commit()


class FakeGarmin:
    def __init__(self, water):
        self.water, self.calls = water, []

    def get_activity(self, aid):
        self.calls.append(aid)
        w = self.water.get(aid)
        return {"summaryDTO": {"waterEstimated": w} if w else {}}


class FakeMFP:
    """Stands in for collectors.mfp_collector.sweat_ops."""
    def __init__(self, fail=None, auth_after=None):
        self.batches, self.n, self.fail, self.auth_after = [], 0, fail or set(), auth_after

    def __call__(self, ops):
        self.batches.append(ops)
        results = []
        for i, op in enumerate(ops):
            if self.auth_after is not None and i >= self.auth_after:
                results.append({"key": op["key"], "op": op["op"], "ok": False, "error": "HTTP 401"})
                return {"ok": False, "auth": True, "error": "HTTP 401", "results": results}
            if (op["key"], op["op"]) in self.fail:
                results.append({"key": op["key"], "op": op["op"], "ok": False, "error": "HTTP 500"})
                continue
            self.n += 1
            r = {"key": op["key"], "op": op["op"], "ok": True}
            if op["op"] == "log":
                r["entry_id"] = f"uuid-{self.n}"
            results.append(r)
        return {"ok": True, "results": results}


@pytest.fixture
def conn(empty_db):
    c = sqlite3.connect(empty_db)
    yield c
    c.close()


def _row(conn, aid):
    return conn.execute("SELECT status, sodium_mg, mfp_entry_id, logged_mg, water_source, mode FROM fuel_sweat_sodium "
                        "WHERE activity_id = ?", (aid,)).fetchone()


def test_candidates_filter_type_window_duration_and_dups(conn):
    _add_act(conn, "1", "2026-10-15")
    _add_act(conn, "2", "2026-10-15", typ="walking")
    _add_act(conn, "3", "2026-10-10")                    # outside the 3-day lookback
    _add_act(conn, "4", "2026-10-16", dur=300)           # under min_minutes
    _add_act(conn, "5", "2026-10-14", dup_of=1)
    _add_act(conn, "6", "2026-10-14", typ="gravel_cycling")
    assert [a["id"] for a in sweat.candidates(conn, _cfg(), TODAY)] == ["6", "1"]
    assert [a["id"] for a in sweat.candidates(conn, _cfg(since="2026-10-15"), TODAY)] == ["1"]


def test_dry_run_records_plan_and_never_calls_mfp(conn):
    _add_act(conn, "1", "2026-10-15")
    mfp = FakeMFP()
    out = sweat.sync(conn, FakeGarmin({"1": 800.0}), _settings(mode="dry_run"), TODAY, sweat_ops=mfp)
    assert mfp.batches == []
    assert out["rides"][0]["sodium_mg"] == 800 and out["rides"][0]["ops"] == ["log"]
    assert _row(conn, "1") == ("planned", 800, None, None, "garmin", "dry_run")


def test_no_concentration_marks_rides_and_sends_nothing(conn):
    _add_act(conn, "1", "2026-10-15")
    mfp = FakeMFP()
    sweat.sync(conn, FakeGarmin({"1": 800.0}), _settings(mode="live", mg_per_l=None), TODAY, sweat_ops=mfp)
    assert mfp.batches == [] and _row(conn, "1")[0] == "needs_concentration"


def test_live_logs_once_then_skips_and_caches_garmin_water(conn):
    _add_act(conn, "1", "2026-10-15")
    g, mfp = FakeGarmin({"1": 800.0}), FakeMFP()
    out = sweat.sync(conn, g, _settings(mode="live"), TODAY, sweat_ops=mfp)
    assert out["mfp"] == "ok"
    assert mfp.batches == [[{"op": "log", "key": "1", "date": "2026-10-15", "mg": 800}]]
    assert _row(conn, "1") == ("logged", 800, "uuid-1", 800, "garmin", "live")
    out = sweat.sync(conn, g, _settings(mode="live"), TODAY, sweat_ops=mfp)
    assert len(mfp.batches) == 1 and out["ops"] == 0      # re-run: nothing to do
    assert g.calls == ["1"]                               # waterEstimated read once
    assert _row(conn, "1")[:4] == ("logged", 800, "uuid-1", 800)


def test_dry_run_after_live_keeps_the_entry(conn):
    _add_act(conn, "1", "2026-10-15")
    g, mfp = FakeGarmin({"1": 800.0}), FakeMFP()
    sweat.sync(conn, g, _settings(mode="live"), TODAY, sweat_ops=mfp)
    sweat.sync(conn, g, _settings(mode="dry_run"), TODAY, sweat_ops=mfp)
    assert _row(conn, "1")[:4] == ("logged", 800, "uuid-1", 800)


def test_changed_estimate_replaces_entry_delete_first(conn):
    _add_act(conn, "1", "2026-10-15")
    g, mfp = FakeGarmin({"1": 800.0}), FakeMFP()
    sweat.sync(conn, g, _settings(mode="live"), TODAY, sweat_ops=mfp)
    sweat.sync(conn, g, _settings(mode="live", mg_per_l=1200), TODAY, sweat_ops=mfp)
    assert mfp.batches[1] == [{"op": "delete", "key": "1", "entry_id": "uuid-1"},
                              {"op": "log", "key": "1", "date": "2026-10-15", "mg": 960}]
    assert _row(conn, "1")[:4] == ("logged", 960, "uuid-3", 960)


def test_failed_delete_keeps_old_id_for_retry(conn):
    _add_act(conn, "1", "2026-10-15")
    g = FakeGarmin({"1": 800.0})
    sweat.sync(conn, g, _settings(mode="live"), TODAY, sweat_ops=FakeMFP())
    sweat.sync(conn, g, _settings(mode="live", mg_per_l=1200), TODAY, sweat_ops=FakeMFP(fail={("1", "delete")}))
    status, mg, eid, logged, *_ = _row(conn, "1")
    assert (status, eid, logged) == ("error", "uuid-1", 800)


def test_failed_log_retries_next_run(conn):
    _add_act(conn, "1", "2026-10-15")
    g = FakeGarmin({"1": 800.0})
    sweat.sync(conn, g, _settings(mode="live"), TODAY, sweat_ops=FakeMFP(fail={("1", "log")}))
    assert _row(conn, "1")[:3] == ("error", 800, None)
    mfp = FakeMFP()
    sweat.sync(conn, g, _settings(mode="live"), TODAY, sweat_ops=mfp)
    assert mfp.batches[0][0]["op"] == "log" and _row(conn, "1")[:3] == ("logged", 800, "uuid-1")


def test_auth_failure_mid_batch_keeps_created_ids(conn):
    _add_act(conn, "1", "2026-10-14")
    _add_act(conn, "2", "2026-10-15")
    out = sweat.sync(conn, FakeGarmin({"1": 800.0, "2": 500.0}), _settings(mode="live"), TODAY,
                     sweat_ops=FakeMFP(auth_after=1))
    assert out["mfp"] == "auth_expired"
    assert _row(conn, "1")[2] == "uuid-1"
    assert _row(conn, "2")[:3] == ("error", 500, None)


def test_estimate_under_min_removes_existing_entry(conn):
    _add_act(conn, "1", "2026-10-15")
    g, mfp = FakeGarmin({"1": 800.0}), FakeMFP()
    sweat.sync(conn, g, _settings(mode="live"), TODAY, sweat_ops=mfp)
    sweat.sync(conn, g, _settings(mode="live", override_mg={"1": 0}), TODAY, sweat_ops=mfp)
    assert mfp.batches[1] == [{"op": "delete", "key": "1", "entry_id": "uuid-1"}]
    assert _row(conn, "1")[:4] == ("skipped", 0, None, None)


def test_off_mode_does_nothing(conn):
    _add_act(conn, "1", "2026-10-15")
    assert sweat.sync(conn, FakeGarmin({}), _settings(mode="off"), TODAY) == {"mode": "off"}


# ------------------------------------------------------------------ MFP-side op runner
class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body, self.text = code, body, ""

    def json(self):
        return self._body


class _Session:
    def __init__(self, post_codes, delete_codes):
        self.post_codes, self.delete_codes, self.calls = list(post_codes), list(delete_codes), []

    def post(self, url, json=None, headers=None):
        self.calls.append(("post", url, json))
        code = self.post_codes.pop(0)
        return _Resp(code, [{"id": f"e{len(self.calls)}"}])

    def delete(self, url, headers=None):
        self.calls.append(("delete", url, None))
        return _Resp(self.delete_codes.pop(0))


def _client(post=(), delete=()):
    return SimpleNamespace(BASE_URL_SECURE="https://www.myfitnesspal.com/", session=_Session(post, delete))


def test_run_ops_payload_matches_verified_request():
    c = _client(post=[201])
    res, auth = S.run_ops(c, [{"op": "log", "key": "1", "date": "2026-10-15", "mg": 987}], lambda cl, x: x)
    assert auth is None and res == [{"key": "1", "op": "log", "ok": True, "entry_id": "e1"}]
    _, url, body = c.session.calls[0]
    assert url == "https://www.myfitnesspal.com/api/services/diary"
    item = body["items"][0]
    assert item["food"] == {"id": "274488977432117", "version": "274488977432117"}
    assert item["servings"] == 987 and item["meal_position"] == 3 and item["date"] == "2026-10-15"


def test_run_ops_skips_log_after_failed_delete_of_same_ride():
    c = _client(post=[201], delete=[500])
    res, _ = S.run_ops(c, [{"op": "delete", "key": "1", "entry_id": "u"},
                           {"op": "log", "key": "1", "date": "2026-10-15", "mg": 900}], lambda cl, x: x)
    assert [r["ok"] for r in res] == [False, False] and "skipped" in res[1]["error"]
    assert [k for k, *_ in c.session.calls] == ["delete"]


def test_run_ops_delete_404_counts_as_done():
    res, _ = S.run_ops(_client(delete=[404]), [{"op": "delete", "key": "1", "entry_id": "u"}], lambda cl, x: x)
    assert res[0]["ok"] is True


def test_run_ops_auth_stops_batch_but_returns_done_ops():
    c = _client(post=[201, 401, 201])
    ops = [{"op": "log", "key": k, "date": "2026-10-15", "mg": 500} for k in ("1", "2", "3")]
    res, auth = S.run_ops(c, ops, lambda cl, x: x)
    assert auth and [r["ok"] for r in res] == [True, False] and res[0]["entry_id"] == "e1"
    assert len(c.session.calls) == 2


def test_mfp_sweat_refuses_without_persist_hook(monkeypatch, capsys):
    monkeypatch.setattr(S, "hook_active", lambda: False)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(read=lambda: '{"ops": []}'))
    assert S.main() == 2
    assert "persist hook" in capsys.readouterr().out


# ------------------------------------------------------------------ fetch / upsert
def _entry(name, sodium):
    return SimpleNamespace(name=name, nutrition_information={"sodium": sodium})


def test_split_sweat_excludes_planner_entries_from_count():
    entries = [_entry("Oatmeal, 1 cup", 120), _entry("Fuel planner - Sweat sodium loss (1 mg), 900 mg sodium", -900)]
    assert F.split_sweat(entries) == (1, 900.0)


def test_sweat_only_day_is_not_a_logged_day():
    days = F.build_days({"Sodium": {"2026-10-15": -900, "2026-10-16": 1400}},
                        {"2026-10-15": 0, "2026-10-16": 3}, {"2026-10-15": 900.0, "2026-10-16": 600.0})
    assert days["2026-10-15"] == {"logged": False, "entries": 0}
    assert days["2026-10-16"]["sodium_mg"] == 1400 and days["2026-10-16"]["sweat_sodium_mg"] == 600.0


def test_upsert_stores_sweat_sodium(empty_db):
    c = sqlite3.connect(empty_db)
    try:
        M.upsert(c, {"2026-10-16": {"logged": True, "entries": 3, "sodium_mg": 1400.0, "sweat_sodium_mg": 600.0}},
                 now="2026-10-16T12:00:00")
        assert c.execute("SELECT sodium_mg, sweat_sodium_mg FROM nutrition_daily").fetchall() == [(1400.0, 600.0)]
    finally:
        c.close()
