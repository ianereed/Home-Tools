"""Garmin Coach (adaptive training plan) → normalized workouts in the engine's step format."""
from __future__ import annotations

import hashlib
import json
from datetime import date


def discover_active_plan(client, today: date) -> dict | None:
    """The adaptive plan that is scheduled/active and hasn't ended."""
    plans = client.get_training_plans() or {}
    lst = plans.get("trainingPlanList", []) if isinstance(plans, dict) else plans
    for p in lst:
        status = (p.get("trainingStatus") or {}).get("statusKey", "")
        end = (p.get("endDate") or "")[:10]
        if status.lower() in ("scheduled", "active", "in_progress") and end and end >= today.isoformat():
            return {"id": p.get("trainingPlanId"), "name": p.get("name"), "category": p.get("trainingPlanCategory"),
                    "start": (p.get("startDate") or "")[:10], "end": end}
    return None


def _steps(raw_steps) -> list:
    out = []
    for s in sorted(raw_steps or [], key=lambda x: x.get("stepOrder", 0)):
        if s.get("type") == "RepeatGroupDTO" or s.get("workoutSteps"):
            out.append({"repeat": int(s.get("numberOfIterations") or s.get("endConditionValue") or 1),
                        "steps": _steps(s.get("workoutSteps"))})
            continue
        dur = float(s.get("endConditionValue") or 0)
        cond = (s.get("endCondition") or {}).get("conditionTypeKey")
        if cond and cond != "time":
            dur = 0.0  # lap-button / distance steps: unknown duration
        lo, hi = s.get("targetValueOne"), s.get("targetValueTwo")
        ttype = (s.get("targetType") or {}).get("workoutTargetTypeKey")
        if ttype != "power.zone" or lo is None or hi is None:
            lo = hi = 0.0
        out.append([dur, float(lo), float(hi)])
    return out


def normalize(task: dict, detail: dict | None) -> dict:
    tw = task.get("taskWorkout") or {}
    ds = task.get("calendarDate")
    if tw.get("restDay"):
        return {"date": ds, "rest": True, "type": tw.get("workoutPhrase") or "REST", "uuid": tw.get("workoutUuid")}
    w = {"date": ds, "uuid": tw.get("workoutUuid"), "name": tw.get("workoutName"),
         "type": tw.get("workoutPhrase") or tw.get("trainingEffectLabel") or "BASE",
         "description": tw.get("workoutDescription"), "duration_s": tw.get("estimatedDurationInSecs"),
         "key": bool(task.get("keyWorkout")), "long": bool(task.get("longWkt"))}
    if detail:
        steps = []
        for seg in detail.get("workoutSegments") or []:
            steps += _steps(seg.get("workoutSteps"))
        if steps:
            w["steps"] = steps
        w["te"] = detail.get("estimatedTrainingEffect")
    if "steps" not in w and w.get("duration_s"):
        # no detail: assume low Z2 so energy is not wildly off
        w["avg_power_w"] = 140.0
    return w


def digest(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def fetch_window(client, plan_id: int, cached_detail=lambda uuid: None) -> list[dict]:
    """Normalized Coach workouts in the published window. cached_detail(uuid) returns a previously
    stored normalized workout (with steps) to avoid re-fetching unchanged workouts."""
    ad = client.get_adaptive_training_plan_by_id(plan_id) or {}
    out = []
    for task in ad.get("taskList") or []:
        tw = task.get("taskWorkout") or {}
        uuid = tw.get("workoutUuid")
        if tw.get("restDay") or not uuid:
            out.append(normalize(task, None))
            continue
        cached = cached_detail(uuid)
        if cached and cached.get("steps"):
            w = normalize(task, None)
            w["steps"], w["te"] = cached["steps"], cached.get("te")
            w.pop("avg_power_w", None)
        else:
            detail = client.connectapi(f"/workout-service/fbt-adaptive/{uuid}")
            w = normalize(task, detail)
        out.append(w)
    return sorted(out, key=lambda w: w["date"])
