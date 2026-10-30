# Coaching plan (Claude replaces Garmin Coach, from 2026-11-02)

Claude plans Ian's riding. Read this before planning a week or reviewing a ride.

## Goals (sources of truth, not copied here)
- Body fat: `dashboard/targets.py` `BODY_FAT_GOALS` (Goal 1, Goal 2), weight per goal from the latest DEXA
  (`dashboard.metrics.weight_goals`). Deficit pace: fuel settings `pace_schedule`; keep the current deficit.
- Cardiology goals and medications: `cardiology/clinical_data.py` (gitignored). Max-effort work waits on the
  cardiologist's answer about intensity limits (asked 2026-10-30).
- FTP up. Event: Sea Otter 2027 Enduro (mid-April, likely; gravel race is the alternative). A March tune-up
  enduro would help.
- HR max 192 (`dashboard/targets.py` HRMAX). FTP 230 in Garmin is untested until the 11-03 test.

## Constraints
- 10-14 h/week. Smart trainer runs Garmin workouts (structured intervals indoors).
- Sunday group ride weekly at 09:00 (optional; when skipped, lengthen Saturday). Set `ride_by_date` for its time.
- Dog-trailer rides: 1 h max on smooth road, dog-park break in the middle (lap-button step), ~140 W.
- Lifts: 2/week, placed by the fuel planner (`nutrition/strength.py`), with the shoulder PT block appended
  (fuel settings `pt_block`, `pt_minutes`). No heavy barbell/dumbbell overhead pressing (PT weights only).
  No rock climbing until April.
- Travel: Nov 24-Dec 2 (gravel bike, terrain unknown), Dec 19-Jan 31/Feb 7 Seattle/Bellingham (gravel bike,
  winter; trainer may come along).

## Season
| Dates | Block | Focus |
|---|---|---|
| Nov 2-8 | Test week | 20-min FTP test (Tue), 5-min + 1-min test (Thu); set FTP from the 20-min (x0.95) |
| Nov 9-23 | Build 1 | 10 -> 12 h; 2 quality days (sweet spot / threshold); rest Z2 |
| Nov 24-Dec 2 | Trip 1 | Lighter / recovery; rides by time + RPE, one fartlek gravel ride |
| Dec 3-18 | Build 2 | up to 14 h; threshold + first VO2 max |
| Dec 19-early Feb | Winter | With trainer: indoor build block. Without: maintenance + gravel when possible |
| Feb-mid Apr | Enduro-specific | VO2 max, anaerobic capacity (30/30s, 1-min @150%), MTB skills, tune-up race, 10-day taper; deficit ends 2-3 weeks before the race |

Weekly shape: two hard trainer days (lifts land on them, after the ride), dog rides on easy days, long Z2
Saturday, group ride Sunday, Monday rest/recovery. ~80% of time easy. 3 build weeks + 1 easier week.

## Progression rules
- Hit every rep at the top of the target with RPE <= 6 -> next session +3-5% targets or +1 rep. Missed reps or
  RPE >= 9 -> repeat. Change one thing at a time (targets, reps, or hours).
- Anaerobic days are genuinely anaerobic: all-out or short rests. Today's-style 6x30s with 5-min rests is peak
  power, not capacity (2026-10-30: averaged 437 W, last rep 507 W, RPE 4).
- Morning check: sleep, HRV, Garmin readiness. Low -> swap the hard day for Z2 or move it.
- Review every ride against its targets (typed splits) and adjust the next session.

## Mechanics
- Upload cycling workouts with `power.zone` (workoutTargetTypeId 2) and targetValueOne/Two in **watts**
  (verified; id 6 `power.between` gets stored as pace). Lap-button step: conditionTypeId 1.
- The fuel planner reads every non-`FP ·` cycling workout on the Garmin calendar (`nutrition/garmin_rides.py`);
  when Coach and Claude both have a ride on a day, the harder wins. `push_generated_rides` is false.
- One-off changes: fuel settings `ride_by_date` ("rest" or {start, minutes, avg_power_w, name}) and
  `lift_start_by_date`. Google Calendar mirrors Garmin (`nutrition/gcal_workouts.py`).

## Workout library (Garmin workout ids)
| Workout | id |
|---|---|
| FTP test · 20 min | 1746078627 |
| Power test · 5 min + 1 min | 1746078629 |
| Dog trailer ride · 1h Z2 | 1746078632 |
| Recovery spin · 45 min | 1746078640 |
| Long Z2 · 3h | 1746078646 |
| Group ride · 2h aerobic | 1745984637 |
