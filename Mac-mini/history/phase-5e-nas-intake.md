# Phase 5e — nas-intake v1 (DONE 2026-04-29 23:47)

Mac mini LaunchAgent `com.home-tools.nas-intake` (5-min `StartInterval`)
watches `~/Share1/**/[Ii]ntake/` folders, OCR + classifies via subprocess to
event-aggregator's `ingest-image` (NAS_WRITE_DISABLED=1), and files
under the parent (`<parent>/<year>/<doc-type>/<date>_<slug>/`) with
per-parent JOURNAL.md + journal.jsonl. Calendar events extracted from
intake-dropped docs come for free via the subprocess — they appear on
the daily Slack proposal dashboard like Slack-uploaded files do.

Verified end-to-end with a real medical PDF (
`Healthcare/0-Ian Healthcare/Intake/My Health Online - Appointment Details.pdf`)
→ filed at `Healthcare/0-Ian Healthcare/2026/Forms/2026-05-04_my-health-online---appointment-details/`,
JOURNAL.md + journal.jsonl appended, source archived to
`Intake/_processed/2026-04/`, calendar event proposed on Slack.

Source at `~/Home-Tools/nas-intake/`. v2 will add HEIC support, Slack
hold-in-_review on classifier mismatch, and quarantine-after-N-failures.

(v1.1 followed on 2026-04-30 — large-file path with heartbeat watchdog +
service-monitor surfacing of wedged files.)
