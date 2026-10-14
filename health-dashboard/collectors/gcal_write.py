"""Google Calendar writes for the Garmin → Calendar sync. Runs in event-aggregator's venv (Google libs).

    <event-aggregator>/.venv/bin/python -I collectors/gcal_write.py < request.json

Request (stdin JSON), either or both parts:
  {"calendar": "primary", "tag": "garmin-sync",
   "list": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
   "ops": [{"op": "insert", "key": K, "body": {...}},
           {"op": "patch",  "key": K, "event_id": ID, "body": {...}},
           {"op": "delete", "key": K, "event_id": ID}]}
Response (stdout JSON):
  {"ok": true, "existing": [{"id", "key", "digest", "date", "summary"}], "results": [{"key", "op", "ok", "event_id"|"error"}]}

Only events carrying extendedProperties.private.source == tag are ever listed; the caller only ever
patches/deletes ids that came back from such a listing, so nothing else on the calendar is touched.
Reuses event-aggregator's OAuth token (calendar.events scope).
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


def _svc():
    ea = Path(os.environ.get("EVENT_AGGREGATOR_DIR", Path(__file__).resolve().parents[2] / "event-aggregator"))
    sys.path.insert(0, str(ea))
    os.chdir(ea)
    import config as ea_config  # noqa
    from connectors.google_auth import get_credentials  # noqa
    from googleapiclient.discovery import build  # noqa

    creds = get_credentials(["https://www.googleapis.com/auth/calendar.events"], ea_config.GCAL_TOKEN_JSON,
                            ea_config.GMAIL_CREDENTIALS_JSON, "gcal_token")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _existing(svc, cal: str, tag: str, start: str, end: str) -> list[dict]:
    out, token = [], None
    while True:
        resp = svc.events().list(calendarId=cal, timeMin=f"{start}T00:00:00-08:00", timeMax=f"{end}T23:59:59-08:00",
                                 singleEvents=True, showDeleted=False, maxResults=250, pageToken=token,
                                 privateExtendedProperty=[f"source={tag}"]).execute()
        for e in resp.get("items", []):
            priv = (e.get("extendedProperties") or {}).get("private") or {}
            st = e.get("start") or {}
            out.append({"id": e["id"], "key": priv.get("garmin_key"), "digest": priv.get("digest"),
                        "date": (st.get("dateTime") or st.get("date") or "")[:10], "summary": e.get("summary"),
                        "status": e.get("status")})
        token = resp.get("nextPageToken")
        if not token:
            return out


def _apply(svc, cal: str, ops: list[dict]) -> list[dict]:
    from googleapiclient.errors import HttpError  # noqa

    results = []
    for op in ops:
        r = {"key": op.get("key"), "op": op["op"], "ok": True}
        try:
            if op["op"] == "insert":
                r["event_id"] = svc.events().insert(calendarId=cal, body=op["body"]).execute()["id"]
            elif op["op"] == "patch":
                r["event_id"] = svc.events().patch(calendarId=cal, eventId=op["event_id"], body=op["body"]).execute()["id"]
            elif op["op"] == "delete":
                try:
                    svc.events().delete(calendarId=cal, eventId=op["event_id"]).execute()
                except HttpError as e:
                    if e.resp.status not in (404, 410):  # already gone is fine
                        raise
                r["event_id"] = op["event_id"]
            else:
                raise ValueError(f"unknown op {op['op']}")
        except Exception as e:  # one bad op must not abort the rest
            r.update(ok=False, error=f"{type(e).__name__}: {str(e)[:300]}")
        results.append(r)
    return results


def main() -> int:
    socket.setdefaulttimeout(20)
    req = json.load(sys.stdin)
    cal, tag = req.get("calendar", "primary"), req.get("tag", "garmin-sync")
    try:
        svc = _svc()
        out: dict = {"ok": True}
        if req.get("list"):
            out["existing"] = _existing(svc, cal, tag, req["list"]["start"], req["list"]["end"])
        if req.get("ops"):
            out["results"] = _apply(svc, cal, req["ops"])
        print(json.dumps(out))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
