"""Fetch Google Calendar events and print JSON. Runs in event-aggregator's venv (Google client libs).

    <event-aggregator>/.venv/bin/python -I collectors/gcal_fetch.py --calendar ID [--calendar ID] --start D --end D

Reuses event-aggregator's OAuth token (calendar.events scope; read-only use here).
Output: {"ok": true, "events": [{calendar, summary, start, end, all_day, location, transparency,
         self_status, event_type, status}]}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calendar", action="append", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args(argv)
    import socket
    import time

    socket.setdefaulttimeout(15)  # a hung token refresh / API call fails fast and gets one retry
    ea = Path(os.environ.get("EVENT_AGGREGATOR_DIR", Path(__file__).resolve().parents[2] / "event-aggregator"))
    sys.path.insert(0, str(ea))
    os.chdir(ea)
    for attempt in (1, 2):
        rc = _fetch(a, attempt == 2)
        if rc is not None:
            return rc
        time.sleep(2)
    return 2


def _fetch(a, last: bool):
    """Returns an exit code, or None to retry after a network timeout."""
    try:
        import config as ea_config  # noqa
        from connectors.google_auth import get_credentials  # noqa
        from googleapiclient.discovery import build  # noqa

        creds = get_credentials(["https://www.googleapis.com/auth/calendar.events"], ea_config.GCAL_TOKEN_JSON,
                                ea_config.GMAIL_CREDENTIALS_JSON, "gcal_token")
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        events = []
        for cal in a.calendar:
            token = None
            while True:
                resp = svc.events().list(calendarId=cal, timeMin=f"{a.start}T00:00:00-08:00", timeMax=f"{a.end}T23:59:59-08:00",
                                         singleEvents=True, orderBy="startTime", maxResults=250, pageToken=token).execute()
                for e in resp.get("items", []):
                    me = next((x for x in e.get("attendees", []) if x.get("self")), None)
                    st, en = e.get("start", {}), e.get("end", {})
                    events.append({"calendar": cal, "summary": e.get("summary", ""), "status": e.get("status"),
                                   "start": st.get("dateTime") or st.get("date"), "end": en.get("dateTime") or en.get("date"),
                                   "all_day": "date" in st, "location": e.get("location"),
                                   "transparency": e.get("transparency", "opaque"),
                                   "self_status": me.get("responseStatus") if me else None,
                                   "event_type": e.get("eventType"),
                                   "source_tag": ((e.get("extendedProperties") or {}).get("private") or {}).get("source")})
                token = resp.get("nextPageToken")
                if not token:
                    break
        print(json.dumps({"ok": True, "events": events}))
        return 0
    except (TimeoutError, OSError) as e:
        if not last:
            return None
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}))
        return 2
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
