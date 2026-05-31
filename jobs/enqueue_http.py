"""
HTTP enqueue server — :8504, Tailscale-bound.

Why a separate server (vs the Mini Ops console at :8503):
  - The console is Streamlit and needs the full process lifecycle for sessions.
  - HTTP enqueue is stateless POST/GET with token auth — fits stdlib http.server.
  - Splitting them means the console can crash + KeepAlive-restart without
    breaking iPhone Shortcuts or Claude sessions (TC6).

Endpoints:
  GET  /kinds                  list registered Job kinds
  GET  /jobs/<id>              fetch a result: {status: pending|success|error, result, error}
  POST /jobs                   enqueue {kind, params}; returns {id}
  GET  /healthz                liveness for the LaunchAgent

Auth: `Authorization: Bearer <token>` against $HOME_TOOLS_HTTP_TOKEN.
Bound to tailscale0 only — `--host 100.x.y.z` from the install script.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
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

_IPHONE_INTENTS = frozenset({"save", "save_and_shop", "shop_only"})


def _parse_multipart(body: bytes, boundary: bytes) -> dict[str, dict]:
    """Tiny multipart/form-data parser. Returns {name: {"value": bytes, "filename": str|None}}.

    Stdlib's `cgi.FieldStorage` is deprecated and removed in 3.13; we only need
    to support the two field shapes the iPhone Shortcut sends (a file part +
    short text parts), so a hand-rolled parser is the lowest-dependency path.
    """
    delim = b"--" + boundary
    end = b"--" + boundary + b"--"

    # Strip any preamble before the first delimiter; treat \r\n and \n as line
    # separators since Shortcuts can be sloppy.
    parts: dict[str, dict] = {}
    idx = body.find(delim)
    if idx < 0:
        return parts

    body = body[idx + len(delim):]
    while True:
        # Skip the CRLF (or LF) immediately after the delimiter.
        if body.startswith(b"\r\n"):
            body = body[2:]
        elif body.startswith(b"\n"):
            body = body[1:]
        if body.startswith(b"--"):
            break

        # Split headers / payload at the first blank line.
        header_end = body.find(b"\r\n\r\n")
        sep_len = 4
        if header_end < 0:
            header_end = body.find(b"\n\n")
            sep_len = 2
            if header_end < 0:
                break
        raw_headers = body[:header_end].decode("utf-8", errors="replace")
        body = body[header_end + sep_len:]

        # Find next delimiter — payload ends at the byte before its CRLF.
        next_delim = body.find(delim)
        if next_delim < 0:
            break
        payload = body[:next_delim]
        # Trim the trailing CRLF (or LF) that separates payload from the delimiter line.
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        elif payload.endswith(b"\n"):
            payload = payload[:-1]

        # Parse Content-Disposition for the field name + optional filename.
        name = None
        filename = None
        for line in raw_headers.split("\n"):
            line = line.strip()
            if line.lower().startswith("content-disposition"):
                m_name = re.search(r'name="([^"]*)"', line)
                m_file = re.search(r'filename="([^"]*)"', line)
                if m_name:
                    name = m_name.group(1)
                if m_file:
                    filename = m_file.group(1)
                break
        if name is not None:
            parts[name] = {"value": payload, "filename": filename}

        # Step past the delimiter we just hit.
        body = body[next_delim + len(delim):]
        if body.startswith(b"--"):
            break

    return parts


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
        if not hmac.compare_digest(header[len("Bearer "):], token):
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
            from jobs.lib import get_baseline, get_requires
            kinds = _registered_kinds()
            out = []
            for name, fn in sorted(kinds.items()):
                bl = get_baseline(fn)
                req = get_requires(fn)
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
        if parsed.path == "/queue-size":
            from jobs import huey as _huey
            try:
                self._send_json(200, {"size": _huey.storage.queue_size()})
            except Exception as exc:
                self._send_json(500, {"error": f"queue_size failed: {exc}"})
            return
        if parsed.path.startswith("/jobs/"):
            job_id = parsed.path[len("/jobs/"):]
            if not job_id:
                self._send_json(404, {"error": "missing job id"})
                return
            from jobs import huey as _huey
            try:
                result = _huey.result(job_id, blocking=False, preserve=True)
            except Exception as exc:
                self._send_json(
                    200,
                    {
                        "status": "error",
                        "result": None,
                        "error": f"task crashed: {type(exc).__name__}: {exc}",
                    },
                )
                return
            if result is None:
                self._send_json(200, {"status": "pending", "result": None, "error": None})
            else:
                self._send_json(200, {"status": "success", "result": result, "error": None})
            return
        self._send_json(404, {"error": f"unknown path {parsed.path!r}"})

    def do_POST(self) -> None:
        if not self._check_auth():
            return
        if self.path == "/iphone-intake":
            self._handle_iphone_intake()
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

    def _handle_iphone_intake(self) -> None:
        """Phase 21: accept a multipart photo upload from the iPhone Shortcut.

        Form fields:
          photo   — file part (image/jpeg or image/png)
          intent  — "save" | "save_and_shop" | "shop_only"
          servings — optional, defaults to "4"

        Side effects: writes the photo to IPHONE_INTAKE_DIR/_processing/<sha>.jpg,
        records an intake row, enqueues meal_planner_iphone_intake.
        Returns 202 {"id", "sha", "status"} or {"status": "duplicate", "sha"} on 200.
        """
        ctype = self.headers.get("Content-Type", "")
        m = re.match(r"multipart/form-data\s*;\s*boundary=(.+)", ctype, re.IGNORECASE)
        if not m:
            self._send_json(400, {"error": "expected multipart/form-data"})
            return
        boundary = m.group(1).strip().strip('"').encode()

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._send_json(400, {"error": "empty body"})
            return
        body = self.rfile.read(length)

        parts = _parse_multipart(body, boundary)
        if "photo" not in parts or not parts["photo"]["value"]:
            self._send_json(400, {"error": "missing 'photo' part"})
            return
        photo_bytes = parts["photo"]["value"]

        intent_part = parts.get("intent")
        if intent_part is None or not intent_part["value"]:
            self._send_json(400, {"error": "missing 'intent' part"})
            return
        intent = intent_part["value"].decode("utf-8", errors="replace").strip()
        if intent not in _IPHONE_INTENTS:
            self._send_json(
                400,
                {"error": f"bad intent {intent!r}; expected one of {sorted(_IPHONE_INTENTS)}"},
            )
            return

        servings = 4
        servings_part = parts.get("servings")
        if servings_part and servings_part["value"]:
            try:
                servings = int(servings_part["value"].decode("utf-8").strip())
                if servings <= 0:
                    raise ValueError("non-positive")
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"error": "'servings' must be a positive integer"})
                return

        sha = hashlib.sha256(photo_bytes).hexdigest()[:16]

        from jobs.kinds.meal_planner_iphone_intake import iphone_intake_dir
        intake_dir = iphone_intake_dir()
        processing_dir = intake_dir / "_processing"
        processing_dir.mkdir(parents=True, exist_ok=True)
        photo_path = processing_dir / f"{sha}.jpg"

        from meal_planner.vision import intake_db
        first_time = intake_db.record_intake(
            sha,
            source_path=parts["photo"].get("filename") or "iphone-shortcut",
            nas_path=str(photo_path),
            source="iphone",
        )
        if not first_time:
            self._send_json(200, {"status": "duplicate", "sha": sha, "id": None})
            return

        try:
            photo_path.write_bytes(photo_bytes)
        except OSError as exc:
            self._send_json(500, {"error": f"could not write photo: {exc}"})
            return

        from jobs.kinds.meal_planner_iphone_intake import meal_planner_iphone_intake
        try:
            result = meal_planner_iphone_intake(sha, intent, servings)
        except Exception as exc:
            self._send_json(
                500,
                {"error": f"enqueue failed: {type(exc).__name__}: {exc}"},
            )
            return
        self._send_json(
            202,
            {"id": getattr(result, "id", None), "sha": sha, "status": "enqueued"},
        )

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
