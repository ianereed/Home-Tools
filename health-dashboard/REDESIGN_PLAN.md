# Health Dashboard Redesign + Bulletproofing — Execution Plan

**Date:** 2026-05-31
**Mode:** auto (Opus)
**Driver decision:** Garmin now owns training recommendations. This dashboard
becomes **information-only / holistic historical tracking** — not checked daily.
Restructure the homescreen + data screens for periodic check-ins; make collection
bulletproof; visual polish.

Scope: open to bigger restructuring. Health stays isolated at `:8501` (no console
integration).

---

## Decisions locked in (my calls, for the record)

- **Visual direction:** genuine visual refresh (shared chart-styling helper,
  cleaner theme/typography) + `/design-review` pass on live `:8501` after deploy.
- **"Since you last looked":** track the *actual* last visit via a tiny
  `data/dashboard_state.json` (last-seen timestamp written on load); degrade
  gracefully to a fixed "vs. 30 days ago" comparison if absent.
- **Keep the recovery engine math** (TRIMP / CTL / ATL / TSB) — it's a great
  *historical* fitness signal. **Remove the prescriptive coaching** (train-hard /
  rest-day banners, `get_training_recommendation`).

---

## Workstream A — Bulletproof collection (P0)

Goal: when you're *not* watching daily, the system reliably collects and loudly
tells you when it can't.

- **A0 — Live-state recon (first, on the mini).** SSH to `homeserver@homeserver`,
  check whether the old `com.health-dashboard.collect` / `.staleness` LaunchAgents
  are *still loaded* alongside the huey kinds (possible double-run today). Capture
  `launchctl list | grep health`, the jobs `/kinds` lane report, and run
  `collectors.staleness_check` manually + under the consumer to reproduce rc=1.
- **A1 — Kill the silent-failure pattern** in `jobs/kinds/health_collect.py` and
  `health_staleness.py`:
  - Capture **full** stderr to `health-dashboard/logs/*.log` (stop truncating to 200).
  - Only `record_fire()` on a *successful* run; on failure, `raise` so huey marks
    the task errored (and the verifier/daily-digest can see it).
- **A2 — Root-cause `health_staleness` rc=1-under-consumer.** Leading hypothesis
  (matches the `launchd PATH lacks /sbin` + missing-binary memory): `diagnose()`
  shells out to `tailscale` / `lsof`, which aren't on the launchd `PATH`, raising
  `FileNotFoundError` → rc=1. Fix: wrap each subprocess in `staleness_check.py` to
  tolerate missing binaries (degrade the diagnosis, don't crash), and/or export a
  fuller `PATH` in the consumer. Verify the reproduction from A0 is resolved.
- **A3 — Network resilience** in collectors: `socket.setdefaulttimeout(30)` at the
  top of `collect_all.main()` so no Garmin/Strava call hangs to the 900s ceiling,
  plus a bounded retry (2 attempts, short backoff) wrapping each collector so a
  single transient blip doesn't cost a day. Minimal, inline — no framework.
- **A4 — Resolve the migration** based on A0: either complete it (disable the old
  plists via `launchctl bootout` + `.disabled`, record baseline) so only the huey
  kinds run, or keep the old plists and disable the huey kinds — whichever the live
  state shows is correct. Update `jobs/install.sh` `migrate-all` notes accordingly.
- **A5 — Failure alerting.** Extend the staleness push so a *collection error*
  (not just stale data age) also pings ntfy — the primary "something broke" signal
  now that you're not eyeballing the dashboard daily.

## Workstream B — Dashboard restructure (information-first)

Replace the 6 prescriptive pages (`Today, Recovery, Sleep, Heart Rate, Activities,
Wellness`) with a historical-first IA:

- **B1 — New nav:** `Overview · Sleep · Heart & HRV · Fitness · Activity · Wellness`.
  Remove standalone `Today`/`Recovery`; fold their *informational* bits elsewhere.
- **B2 — Overview homescreen** (built for "haven't looked in 2 weeks"):
  - **"Since you last looked"** change strip — HRV baseline shift, RHR drift, sleep
    trend, training-volume change — as *observations*, not advice. Uses the engine's
    existing z-scores + the `last_seen` store.
  - **Headline trend tiles** — HRV · RHR · Sleep · Fitness (CTL) · Steps, each a
    sparkline + 30/90-day direction arrow.
  - **Data freshness panel**, prominent (per-source last-update + green/amber/red).
  - **Highlights** — best/worst sleep this month, biggest training week, streaks.
- **B3 — Historical-first data screens:** default range 90d (selector up to "all");
  add monthly-average overlays, weekday patterns (sleep/steps), and baseline bands.
  Demote single-day detail (e.g. sleep-stage pie) to drill-downs.
- **B4 — Fitness screen:** keep the CTL/ATL/TSB "fitness over time" curve, reframed
  as *information* ("Fitness trend"), drop the TSB train/rest prescription text.
- **B5 — Remove prescriptive code paths** in `recovery/advisor.py`
  (`get_training_recommendation`, training banner); keep sleep-debt + last-night as
  info; keep all of `engine.py`.

## Workstream C — Hardening / correctness

- **C1 —** Parameterize the `activity_streams` query (`dashboard/app.py:706`) — no
  more f-string interpolation of `act_id`.
- **C2 —** Make the `apple_health_server.py` sleep accumulator stateless (drop the
  `_process_sleep._nights` function attribute; pass state explicitly).
- **C3 —** (folded into A2) guard staleness subprocess calls for missing binaries.

## Workstream D — Visual polish

- **D1 —** Shared chart-styling helper (consistent margins, font, colorway, dark
  theme), tidy CSS, consistent metric/section styling. After deploy, run
  `/design-review` against live `:8501` and iterate on findings.

## Workstream E — Test, deploy, verify, document

- **E1 — Local smoke test** against a *synthetic* `health.db` (generated fake rows)
  so I never surface real personal health values in this conversation; verify the
  app imports and every page renders without error.
- **E2 — Deploy:** branch → commit → push `main`; SSH mini, `git pull`, restart
  `com.health-dashboard.streamlit`, and `launchctl kickstart -kp` the jobs consumer
  (+ jobs-http) after any kind change.
- **E3 — Live verify:** `/browse` or `/design-review` on `homeserver:8501`
  (layout-focused; mindful of real data in screenshots), confirm collection ran.
- **E4 — Docs:** update `health-dashboard/README.md` (new IA, drop Intervals.icu
  drift), refresh `project_health_dashboard.md` memory, journal throughout.

---

## Sequencing (auto)

1. Branch `feat/health-dashboard-redesign`.
2. **A0 recon** on the mini (informs A4) → A1, A2, A3, A5.
3. **C1, C2** (quick correctness).
4. **B1–B5** dashboard restructure (the bulk).
5. **D1** visual polish.
6. **E1** synthetic smoke test.
7. **E2** deploy → **E3** live verify + `/design-review` iterate.
8. **A4** migration resolution (touches live agents — done carefully post-deploy).
9. **E4** docs + memory + journal.

## Risk notes

- Touching live LaunchAgents on the mini: follow the "don't unload mid-flight"
  rule; check state before any `bootout`; do migration resolution as a discrete,
  reversible step.
- Privacy: real health values stay on the mini. Local tests use synthetic data;
  live verification is layout-focused.
- The dashboard rewrite is large — I'll keep `engine.py` intact and verify the
  recovery math still feeds the (now informational) Fitness screen unchanged.
