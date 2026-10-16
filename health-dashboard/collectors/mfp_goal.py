"""Set today's MyFitnessPal nutrition goal from the fuel plan. Runs in the MFP venv; prints JSON.

    <mfp-venv>/bin/python -I collectors/mfp_goal.py --date D --kcal N --carbs G --protein G --fat G
                                                     [--sodium MG] [--cholesterol MG] [--fiber G]

Posts a v2 nutrient goal valid from --date (same request MFP's web app makes; python-myfitnesspal's
set_new_goal does this for today only). Every daily goal row gets the same values, so MFP's mobile
app shows today's plan target; earlier days keep the goal they had. Extra nutrients are only set
when MFP's goal payload already has that field. The rotated session cookie is written back after
the request (see mfp_session.py); the headless auto-refresh is deliberately not used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from . import mfp_session
except ImportError:  # run as a script under -I: the package isn't on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mfp_session

EXTRA = {"sodium": "sodium", "cholesterol": "cholesterol", "fiber": "fiber"}


def build_payload(current: dict, day: str, kcal: float, carbs: float, protein: float, fat: float,
                  extras: dict[str, float]) -> dict:
    item = dict(current["items"][0])
    for k in ("valid_to", "default_group_id", "updated_at"):
        item.pop(k, None)
    item["valid_from"] = day

    def fill(goal: dict) -> dict:
        goal = dict(goal)
        goal["meal_goals"] = []
        goal.pop("group_id", None)
        goal["energy"] = {"value": round(kcal), "unit": "calories"}
        goal["carbohydrates"], goal["protein"], goal["fat"] = round(carbs), round(protein), round(fat)
        for k, v in extras.items():
            if v is not None and k in goal:
                goal[k] = round(v)
        return goal

    item["default_goal"] = fill(item["default_goal"])
    item["daily_goals"] = [fill(g) for g in item.get("daily_goals", [])]
    return {"item": item}


def _post(client, payload: dict):
    headers = dict(client.session.headers)
    headers.update({"authorization": f"Bearer {client.access_token}", "mfp-client-id": "mfp-main-js",
                    "mfp-user-id": f"{client.user_id}"})
    return client.session.post(client.BASE_API_URL + "v2/nutrient-goals", data=json.dumps(payload), headers=headers)


def set_goal(a) -> dict:
    from myfitnesspal_mcp.mfp_client import get_client  # noqa
    c = get_client()
    cur = c._get_request_for_url(c.BASE_API_URL + f"v2/nutrient-goals?date={a.date}", send_token=True).json()
    extras = {"sodium": a.sodium, "cholesterol": a.cholesterol, "fiber": a.fiber}
    payload = build_payload(cur, a.date, a.kcal, a.carbs, a.protein, a.fat, extras)
    r = _post(c, payload)
    session = mfp_session.persist_rotated(c)
    if r.status_code in (401, 403):
        raise PermissionError(f"MFP goal POST rejected: HTTP {r.status_code}")
    if not r.ok:
        raise RuntimeError(f"MFP goal POST failed: HTTP {r.status_code} {r.text[:150]}")
    applied = [k for k, v in extras.items() if v is not None and k in payload["item"]["default_goal"]]
    return {"ok": True, "date": a.date, "kcal": round(a.kcal), "extras_applied": applied, "session": session}


def _is_auth(e: Exception, is_auth_error) -> bool:
    """Session problems only -- a programming error must not be reported as an expired login."""
    if isinstance(e, (TypeError, AttributeError, KeyError, ValueError, NameError)):
        return False
    return isinstance(e, PermissionError) or "NotConnected" in type(e).__name__ or bool(is_auth_error(e))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    for name in ("date",):
        ap.add_argument(f"--{name}", required=True)
    for name in ("kcal", "carbs", "protein", "fat"):
        ap.add_argument(f"--{name}", type=float, required=True)
    for name in ("sodium", "cholesterol", "fiber"):
        ap.add_argument(f"--{name}", type=float)
    a = ap.parse_args(argv)
    try:
        from myfitnesspal_mcp.mfp_client import is_auth_error  # noqa
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"mfp-mcp not importable: {e}", "auth": False}))
        return 2
    try:
        print(json.dumps(set_goal(a)))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}",
                          "auth": _is_auth(e, is_auth_error)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
