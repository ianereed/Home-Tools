"""
HTTP enqueue server — :8504, Tailscale-bound.

Why a separate server (vs the Mini Ops console at :8503):
  - The console is Streamlit and needs the full process lifecycle for sessions.
  - HTTP enqueue is stateless POST/GET with token auth — fits stdlib http.server.
  - Splitting them means the console can crash + KeepAlive-restart without
    breaking iPhone Shortcuts or Claude sessions (TC6).

Endpoints:
  GET  /kinds                  list registered Job kinds
  GET  /jobs/<id>              fetch a result by id (in-memory; huey owns it)
  POST /jobs                   enqueue {kind, params}; returns {id}
  GET  /healthz                liveness for the LaunchAgent

Auth: `Authorization: Bearer <token>` against $HOME_TOOLS_HTTP_TOKEN.
Bound to tailscale0 only — `--host 100.x.y.z` from the install script.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Ensure repo importable when run as a script from launchd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8504
DEFAULT_HOST = "127.0.0.1"  # install.sh overrides to tailscale0 IP


class JobsHandler(BaseHTTPRequestHandler):
    """Single-threaded handler. Hobby workload — no concurrency needed."""

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _check_auth(self) -> bool:
        if self.path == "/healthz":
            return True
        token = os.environ.get("HOME_TOOLS_HTTP_TOKEN")
        if not token:
            self._send_json(500, {"error": "server misconfigured: HOME_TOOLS_HTTP_TOKEN not set"})
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            self._send_json(401, {"error": "missing bearer token"})
            return False
        if header[len("Bearer "):] != token:
            self._send_json(401, {"error": "bad token"})
            return False
        return True

    def do_GET(self) -> None:
        if not self._check_auth():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if parsed.path == "/kinds":
            from jobs.cli import _registered_kinds
            kinds = _registered_kinds()
            out = []
            for name, fn in sorted(kinds.items()):
                bl = getattr(fn, "_baseline", None)
                req = getattr(fn, "_requires", None)
                out.append({
                    "name": name,
                    "baseline": (
                        {"metric": bl.metric, "window": bl.divergence_window}
                        if bl else None
                    ),
                    "requires": req.items if req else [],
                })
            self._send_json(200, {"kinds": out})
            return
        if parsed.path.startswith("/jobs/"):
            job_id = parsed.path[len("/jobs/"):]
            # huey doesn't expose result-by-id lookup directly; tell the
            # caller to use the consumer logs / console for now.
            self._send_json(
                501,
                {"error": "result lookup not implemented in v1; check Mini Ops :8503/Jobs"},
            )
            return
        self._send_json(404, {"error": f"unknown path {parsed.path!r}"})

    def do_POST(self) -> None:
        if not self._check_auth():
            return
        if self.path != "/jobs":
            self._send_json(404, {"error": f"unknown path {self.path!r}"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._send_json(400, {"error": "empty body"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"bad JSON: {exc}"})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"error": "body must be a JSON object"})
            return
        kind = body.get("kind")
        params = body.get("params") or {}
        if not kind:
            self._send_json(400, {"error": "missing 'kind'"})
            return
        if not isinstance(params, dict):
            self._send_json(400, {"error": "'params' must be a JSON object"})
            return

        from jobs.cli import _registered_kinds
        kinds = _registered_kinds()
        fn = kinds.get(kind)
        if fn is None:
            self._send_json(
                404,
                {"error": f"unknown kind {kind!r}", "available": sorted(kinds.keys())},
            )
            return

        # Internal kinds (verifier) are not user-callable.
        if hasattr(fn, "func") and "_internal" in getattr(fn.func, "__module__", ""):
            self._send_json(403, {"error": f"kind {kind!r} is internal"})
            return

        try:
            result = fn(**params) if params else fn()
        except Exception as exc:
            self._send_json(500, {"error": f"enqueue failed: {type(exc).__name__}: {exc}"})
            return
        self._send_json(202, {"id": getattr(result, "id", None), "kind": kind})

    def log_message(self, format: str, *args: Any) -> None:
        # Route http.server's stdout chatter through logging.
        logger.info("http %s - %s", self.address_string(), format % args)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = HTTPServer((host, port), JobsHandler)
    logger.info("jobs.enqueue_http listening on %s:%d", host, port)
    server.serve_forever()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("JOBS_HTTP_HOST", DEFAULT_HOST))
    p.add_argument("--port", type=int, default=int(os.environ.get("JOBS_HTTP_PORT", DEFAULT_PORT)))
    args = p.parse_args()
    serve(args.host, args.port)
