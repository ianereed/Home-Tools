# Mac-mini history

Completed-phase porting recipes and migration notes. Kept verbatim for future
reference — useful if you ever port a similar project to the mini, or need to
remember why a given path/pattern exists.

The active working plan is `Mac-mini/PLAN.md`. Anything here is done.

| File | What it covers |
|---|---|
| [`phase-5b-health-dashboard.md`](phase-5b-health-dashboard.md) | Porting health-dashboard from laptop → mini (Apr 22). Keychain shim, 4-plist install, TCC gotchas. |
| [`phase-5c-service-monitor.md`](phase-5c-service-monitor.md) | Building service-monitor Streamlit dashboard at `:8502` (Apr 27). |
| [`phase-5d-nas-mount.md`](phase-5d-nas-mount.md) | Mounting `iananny:Share1` at `~/Share1`, recovering from a TCC LAN wedge (Apr 29). |
| [`phase-5e-nas-intake.md`](phase-5e-nas-intake.md) | nas-intake v1 LIVE: drop-folder watcher → OCR via event-aggregator subprocess → file under parent (Apr 29). |
| [`post-cutover-followups.md`](post-cutover-followups.md) | Laptop-side rollback artifact cleanup after the laptop→mini cutover (Apr 22 → Apr 29 window). |
