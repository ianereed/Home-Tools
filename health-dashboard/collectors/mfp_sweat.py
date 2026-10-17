"""Add/remove the fuel planner's negative-sodium MyFitnessPal diary entries. Runs in the MFP venv.

    <mfp-venv>/bin/python -I collectors/mfp_sweat.py < ops.json

stdin: {"ops": [{"op": "log", "key": K, "date": "YYYY-MM-DD", "mg": N} | {"op": "delete", "key": K, "entry_id": U}]}
stdout: {"ok": bool, "results": [{"key": K, "op": ..., "ok": bool, "entry_id"?: U, "error"?: str}], "session": ...}

Each sweat entry is SWEAT_FOOD (a private custom food: 0 kcal, sodium -1 mg per serving) logged to
Snacks with servings = mg lost, through the web app's Next.js API (verified 2026-10-16, journal-192:
POST /api/services/diary -> 201 + entry uuid, DELETE /api/services/diary/{uuid} -> 204; diary totals
and the nutrition reports both carry the negative sodium). Ops run in order, so a replacement is
sent as delete-then-log: a failure leaves the day without an entry (retried next run), never two.

Refuses to run without the venv-wide persist hook (mfp_persist.pth): MFP rotates the session token
on use and a process that doesn't write it back kills the saved session (see mfp_session.py).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib import parse

try:
    from . import mfp_session
except ImportError:  # run as a script under -I: the package isn't on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mfp_session

SWEAT_FOOD = {"id": "274488977432117", "version": "274488977432117"}
SWEAT_SERVING = {"nutrition_multiplier": 1, "unit": "mg sodium", "value": 1}
SWEAT_MEAL_POSITION = 3  # Snacks
SWEAT_NAME_MARK = "Sweat sodium loss"  # in the diary entry name; mfp_fetch keys on it too


def entry_payload(day: str, mg: int) -> dict:
    return {"items": [{"type": "food_entry", "date": day, "food": dict(SWEAT_FOOD), "servings": int(mg),
                       "meal_position": SWEAT_MEAL_POSITION, "serving_size": dict(SWEAT_SERVING)}]}


def hook_active() -> bool:
    try:
        from curl_cffi import requests as r  # noqa
        return bool(getattr(r.Session, "_mfp_persist_hook", False))
    except Exception:
        return False


def run_ops(client, ops: list[dict], api_headers) -> tuple[list[dict], str | None]:
    """-> (per-op results, auth error or None). A rejected session stops the batch, but the results
    of ops already done are still returned so their entry ids aren't lost."""
    url = lambda p: parse.urljoin(client.BASE_URL_SECURE, p)  # noqa: E731
    headers = api_headers(client, {"Content-Type": "application/json", "Accept": "application/json",
                                   "Origin": "https://www.myfitnesspal.com"})
    out, failed = [], set()
    for op in ops:
        res = {"key": op.get("key"), "op": op.get("op")}
        if op.get("key") in failed:  # e.g. the delete of a replacement failed: logging now would duplicate
            res.update(ok=False, error="skipped: an earlier op for this ride failed")
            out.append(res)
            continue
        try:
            if op["op"] == "log":
                r = client.session.post(url("api/services/diary"), json=entry_payload(op["date"], op["mg"]),
                                        headers=headers)
                if r.status_code in (401, 403):
                    raise PermissionError(f"diary POST rejected: HTTP {r.status_code}")
                if r.status_code not in (200, 201):
                    raise RuntimeError(f"diary POST HTTP {r.status_code} {r.text[:150]}")
                body = r.json()
                res.update(ok=True, entry_id=(body[0] if isinstance(body, list) else body)["id"])
            elif op["op"] == "delete":
                r = client.session.delete(url(f"api/services/diary/{op['entry_id']}"), headers=headers)
                if r.status_code in (401, 403):
                    raise PermissionError(f"diary DELETE rejected: HTTP {r.status_code}")
                # 404: already gone (Ian deleted it in the app) -- the goal state is reached either way
                if r.status_code not in (200, 204, 404):
                    raise RuntimeError(f"diary DELETE HTTP {r.status_code} {r.text[:150]}")
                res.update(ok=True, status=r.status_code)
            else:
                raise ValueError(f"unknown op {op['op']!r}")
        except PermissionError as e:
            res.update(ok=False, error=str(e))
            out.append(res)
            return out, str(e)
        except Exception as e:  # one bad op shouldn't drop the others' entry ids
            res.update(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")
            failed.add(op.get("key"))
        out.append(res)
    return out, None


def _is_auth(e: Exception, is_auth_error) -> bool:
    if isinstance(e, (TypeError, AttributeError, KeyError, ValueError, NameError)):
        return False
    return isinstance(e, PermissionError) or "NotConnected" in type(e).__name__ or bool(is_auth_error(e))


def main(argv=None) -> int:
    ops = json.loads(sys.stdin.read() or "{}").get("ops", [])
    if not hook_active():
        print(json.dumps({"ok": False, "error": "MFP persist hook not active in this venv (mfp_persist.pth)",
                          "auth": False}))
        return 2
    try:
        from myfitnesspal_mcp.diary import api_headers  # noqa
        from myfitnesspal_mcp.mfp_client import get_client, is_auth_error  # noqa
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"mfp-mcp not importable: {e}", "auth": False}))
        return 2
    client = None
    try:
        client = get_client()
        results, auth_err = run_ops(client, ops, api_headers)
        out = {"ok": auth_err is None, "results": results, "session": mfp_session.persist_rotated(client)}
        if auth_err:
            out.update(error=auth_err, auth=True)
        print(json.dumps(out))
        return 0 if auth_err is None else 2
    except Exception as e:
        session = mfp_session.persist_rotated(client) if client is not None else "no client"
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}",
                          "auth": _is_auth(e, is_auth_error), "session": session}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
