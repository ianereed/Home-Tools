"""Phase 12.7 — event_aggregator_vision kind sanity checks."""
from __future__ import annotations

from pathlib import Path

from jobs.kinds import event_aggregator_vision as mod
from jobs.lib import get_requires


def test_requires_includes_event_aggregator_dir():
    req = get_requires(mod.event_aggregator_vision)
    assert req is not None
    assert "fs:event-aggregator" in req.items


def test_project_path():
    assert mod.PROJECT.name == "event-aggregator"
    assert mod.PROJECT == Path(__file__).resolve().parents[2] / "event-aggregator"


def test_subprocess_invocation(monkeypatch, tmp_path):
    """Body must call `cli.py run-ocr-job --file <path>` in the project dir."""
    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        return _Result()

    import jobs.lib
    from pathlib import Path
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])
    monkeypatch.setattr(jobs.lib._model_state, "_http_post", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "record_fire", lambda _name: None)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    monkeypatch.setattr(mod, "TOUCH_FILE", Path(tmp_path) / "ea-tv.last")

    job = {"file_path": "/tmp/test_phase127.png"}
    result = mod.event_aggregator_vision.func(job)
    assert result["rc"] == 0
    assert result["file_path"] == "/tmp/test_phase127.png"
    assert "cli.py" in captured["argv"][1]
    assert "run-ocr-job" in captured["argv"]
    assert "--file" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--file") + 1] == "/tmp/test_phase127.png"
    assert captured["cwd"] == str(mod.PROJECT)
