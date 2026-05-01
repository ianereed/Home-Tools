# Future Architecture Upgrade: Orchestration Layer with Opus-Reviewed Auto-Debugging

> **Status:** design document. CEO-reviewed 2026-04-30 — **strangler-fig path approved (P0+P1 only)**, Tier 2 (P2) gated behind real-data evaluation at the 4-8 week mark. See `~/.gstack/projects/ianereed-Home-Tools/ceo-plans/2026-04-30-orchestrator-strangler-fig.md` for the locked scope, accepted cherry-picks (regression tests from Day 1, drift detection from Day 1, daily Pushover summary), and P2 graduation criteria.

> Prereq: `Mac-mini/PLAN.md` Phase 6 (Pushover heartbeat + weekly SSH audit) must be complete first.

> **Companion doc:** `~/.claude/plans/i-am-considering-restructuring-virtual-dream.md` contains the evaluation that led to this design (benefits/cons of an agent layer, framework-verification caveat). This document is the architecture plan for *if and when* you proceed.

---

## Context

Home-Tools currently runs 11 LaunchAgents on a 24 GB M4 Mac mini, with file-system-driven IPC and an observability dashboard (`service-monitor`) that detects but does not act. You want to eventually add an **orchestration layer** that:

1. **Does its expensive thinking when nobody else is using the box** (non-peak hours), but **acts immediately when the system is fully down**.
2. **Generates audit trails and code comments designed for an Opus 4.7 reviewer**, so that when you (or Opus, in your IDE) sit down for a daily/weekly/monthly review, scanning the changelog and cleaning up the smaller LLM's "slop" is fast and unambiguous.
3. **Never silently mutates production code or configs.** Tier separation is the load-bearing invariant — the small LLM proposes, Opus disposes.

The architecture below is built around that asymmetric trust: a cheap-but-noisy autonomous tier producing structured proposals, a higher-trust review tier (you + Opus) accepting/rejecting/promoting them.

---

## Design goals (mapped to your asks)

| Your ask | Design implication |
|---|---|
| Orchestration "on top" | New `orchestrator/` service; reads from existing services, never writes into them except via Tier 1 deterministic recipes |
| Auto-debugging in non-peak hours | Two operating modes: triage (peak) and diagnosis (non-peak); LLM work queued for diagnosis mode |
| Auto-debug in peak if system goes down | "System down" override: heartbeat-missing or crash-loop conditions flip into diagnosis mode regardless of clock |
| Excellent logs for Opus review | Schema-strict JSONL audit + Markdown rollups (daily/weekly/monthly) with TL;DR, drift section, per-incident verbatim model outputs |
| Excellent code comments for Opus | Every auto-generated change tagged `# AUTO: orchestrator <date> <incident-id>`; high-slop-risk changes carry `# REVIEW BY: opus` |
| Opus cleans up "slop" | Proposals never auto-applied; Opus reviews `proposals/` and either applies, refactors, or rejects; recurring patterns get *promoted* into permanent service-side fixes |

---

## Tiered architecture

| Tier | What | Trust | Latency budget | Mutates code? |
|---|---|---|---|---|
| **0 — existing** | LaunchAgents + `service-monitor` + Phase 6 heartbeat/Pushover | High (you wrote it) | Real-time | No |
| **1 — deterministic supervisor recipes** | Rule-engine that runs known fixes (keychain unlock, intake sweep, log-size trip) | High (you wrote it) | Seconds | Operational state only (restart, sweep, unlock) — never source code |
| **2 — LLM orchestrator (the new thing)** | qwen3:14b (or hermes3:8b) reads logs/state, forms hypotheses on novel failures, writes proposals | **Low** (autonomous, smaller model) | Minutes (queued for non-peak) | **No.** Writes to `proposals/` and `audit/` only |
| **3 — Opus 4.7 review** | Periodic IDE sessions where Opus reads changelogs and applies/rejects/promotes proposals | High | Cadence-based (daily/weekly/monthly) | Yes — through normal git workflow |

The load-bearing invariant: **Tier 2 produces artifacts; Tier 3 commits.** If Tier 2 misbehaves, the worst case is a noisy `proposals/` directory, never a corrupted service. This is the asymmetric-trust model that makes autonomous-but-cheap viable.

---

## The `orchestrator/` service

**Location:** `Home-Tools/orchestrator/`

**Inputs (read-only, no callbacks into other services):**
- `service-monitor/services.py:SERVICES` — service registry (reuse, don't duplicate)
- All `*-error.log` and structured logs across services
- Per-service SQLite DBs (read-only) for queue depths
- `Mac-mini/PLAN.md` Phase 6 heartbeat output
- Recipe-fire counters (its own metrics)

**Outputs:**
- `orchestrator/audit/YYYY-MM-DD/incidents.jsonl` — append-only structured audit (one event per line)
- `orchestrator/changelogs/daily.md` — human/Opus-readable rollup
- `orchestrator/changelogs/weekly.md`, `monthly.md` — periodic rollups generated from JSONL
- `orchestrator/proposals/YYYY-MM-DD/<incident-id>.diff` — git-style diff of any code change the LLM suggests
- `orchestrator/proposals/YYYY-MM-DD/<incident-id>.md` — companion narrative for each proposal
- Slack DM (escalation only — Tier 1 fired or system-down)

**State:**
- `orchestrator/state.db` — SQLite, tracks recipe-fire counts, hypothesis-budget remaining per incident, pending-review queue
- `orchestrator/queue/` — non-peak LLM work queue (file-based, mirrors your existing pattern)

**Operating modes:**
- **Triage (peak hours, default 08:00–23:00 weekdays):**
  - Subscribe to signals; run Tier 1 deterministic recipes; enqueue LLM work for later
  - Cheap: ~50 MB Python, no LLM calls
- **Diagnosis (non-peak, 23:00–08:00 + weekends):**
  - Drain the queue; one LLM call per queued item (qwen3:14b already resident)
  - Write incidents.jsonl, daily.md updates, proposals/ entries
  - Capped: ≤N LLM-minutes per night (configurable), so an incident storm doesn't run all night
- **System-down override:** triggers diagnosis mode regardless of clock when:
  - Heartbeat missing >5 min, OR
  - ≥3 KeepAlive agents in restart-loop, OR
  - `service-monitor` Streamlit unreachable for >10 min

---

## Audit / changelog schema (designed for Opus consumption)

### `audit/YYYY-MM-DD/incidents.jsonl`

Append-only, one JSON object per line. Required fields:

```json
{
  "incident_id": "2026-05-12T03-14-22-finance-monitor-watcher",
  "ts_detected": "2026-05-12T03:14:22-07:00",
  "ts_resolved": "2026-05-12T03:14:54-07:00",
  "service": "finance-monitor",
  "subservice": "watcher",
  "symptom_verbatim": "OCR attempt 3/3 failed: Ollama 503 from /api/generate",
  "classification": "known | novel | partial",
  "recipe": "ollama_503_backoff" ,
  "llm_invoked": false,
  "llm_hypothesis_verbatim": null,
  "llm_action_proposed_verbatim": null,
  "action_taken": "wait_30s_retry",
  "outcome": "success | failed | requires-review",
  "slop_risk": "low | medium | high",
  "proposal_path": null,
  "review_required": false,
  "drift_signal": "recipe_fire_count_today=4 (avg=1.2)"
}
```

Rationale for each field:
- `*_verbatim` fields: per your journal convention, paraphrase loses information. Opus needs the actual model output to detect slop patterns.
- `slop_risk`: orchestrator self-rates each LLM-driven action. Medium/high triggers `# REVIEW BY: opus` and surfaces in the daily TL;DR.
- `drift_signal`: recipe firing 4× when the running average is 1.2× is a signal of underlying drift — don't just keep recovering, ask why.

### `changelogs/daily.md` — designed for fast Opus scan

```markdown
# Orchestrator daily report — 2026-05-12

**TL;DR:** 7 incidents, 6 auto-recovered, 1 requires-review. 1 drift anomaly. 0 system-down events.

## Drift anomalies
- `keychain_unlock` recipe fired 6× today (running avg: 1.1×). Possible underlying change in macOS keychain behavior. **Suggest:** investigate root cause; do not just keep auto-recovering.

## Requires-review queue (1)
### Incident 2026-05-12T03:14:22 — finance-monitor.watcher [novel, slop-risk=medium]
- **Symptom:** `OCR attempt 3/3 failed: Ollama 503 from /api/generate`
- **LLM hypothesis:** "Ollama is paging qwen3 out under memory pressure when qwen2.5vl loads concurrently. Suggest reducing keep_alive on qwen2.5vl or pre-warming qwen3 before vision calls."
- **Action proposed:** `proposals/2026-05-12/03-14-22-ollama-keepalive.diff` (8 lines, finance-monitor/config.py)
- **Why review-required:** novel failure pattern + config change to a service the orchestrator doesn't own
- **Self-assessed slop risk:** medium — hypothesis is plausible but unverified

## Auto-recovered (6, no action needed)
- 03:42 dispatcher — keychain_unlock fired, success
- 04:11 event-aggregator — ollama_503_backoff fired, success
- 04:55 dispatcher — keychain_unlock fired, success
- 05:33 finance-monitor — intake_sweep fired (1 file >30min), success
- 06:02 dispatcher — keychain_unlock fired, success
- 07:14 event-aggregator — pre_classifier_timeout retry, success

## Recipe-fire summary
| Recipe | Today | 7-day avg | 30-day avg | Status |
|---|---|---|---|---|
| keychain_unlock | 3 | 1.1 | 0.8 | **DRIFT** |
| ollama_503_backoff | 1 | 1.4 | 1.2 | normal |
| intake_sweep | 1 | 0.7 | 0.6 | normal |
```

Why this format:
- TL;DR is one line: Opus reads it first, decides whether to dive in
- Drift section comes *before* incidents — pattern-level signals deserve attention before symptom-level
- Each requires-review incident includes verbatim hypothesis and proposed-action (Opus can audit reasoning quality directly)
- Auto-recovered section is one-liners only — Opus skims for outliers
- Recipe-fire table is the durability check: which recipes are stable, which are growing

### `changelogs/weekly.md` and `monthly.md`

Generated by rolling up the dailies. Weekly emphasizes:
- Recipes whose 7-day avg crossed thresholds (worth permanent fix?)
- Proposals that were applied vs rejected vs refactored (calibration of the smaller LLM)
- Novel incidents that recurred → candidates for promotion to deterministic recipe

Monthly emphasizes:
- Anti-pattern catalogue: what kinds of slop is the smaller LLM consistently producing?
- Prompt/policy changes Opus made to the orchestrator over the month
- "Promotion ledger": which Tier 2 LLM-handled patterns became Tier 1 deterministic recipes; which Tier 1 recipes were retired because the source service got fixed

---

## Code-comment conventions for Opus reviewability

Every artifact the orchestrator generates carries machine-readable provenance:

```python
# AUTO: orchestrator 2026-05-12 incident=03-14-22-finance-monitor-watcher
# REVIEW BY: opus  slop_risk=medium
# Why: hypothesis is that qwen2.5vl is evicting qwen3 from VRAM during vision
#      calls; reducing keep_alive may help. Unverified — needs A/B.
OLLAMA_VISION_KEEP_ALIVE_S = 5  # was 30
```

Conventions:
- **`# AUTO:`** tag is mandatory on every orchestrator-generated line. `grep -rn "# AUTO:"` reveals the entire surface area Opus needs to review.
- **`# REVIEW BY: opus`** carries `slop_risk` and is mandatory for medium/high risk. Low risk doesn't need it.
- **`# Why:`** is a one-liner — orchestrator's hypothesis in plain English.
- **Always preserve the prior value as a comment** (`was 30`) — Opus must be able to revert without re-reading proposals/.

When Opus applies a proposal:
- Strip `# AUTO:` and `# REVIEW BY:` tags (the change is now human-owned)
- Optionally retain `# Why:` if it's a non-obvious invariant
- Move incident to `audit/applied/` and append outcome to monthly summary

---

## Opus review workflow

Run on a cadence (daily quick / weekly thorough / monthly retrospective). The cadence is yours; the orchestrator just maintains the artifacts.

### Daily (5 min)
1. Open `orchestrator/changelogs/daily.md`
2. Read TL;DR. If 0 require-review and 0 drift, close.
3. Scan drift section. Any recipe at >3× its 7-day avg? Add a TODO to investigate root cause.
4. For each requires-review incident: open the proposal diff, decide.
5. Apply with normal git workflow (`git apply` or manual edit, then commit). Strip `# AUTO:` tags.

### Weekly (15–30 min)
1. Read `weekly.md`
2. Promotion review: any LLM-handled pattern that fired ≥7 times this week? Encode it as a Tier 1 deterministic recipe (and write a regression test).
3. Demotion review: any Tier 1 recipe whose root cause was fixed in the source service this week? Retire it.
4. Calibration: of last week's proposals, how many were applied / refactored / rejected? Apply rate <50% means orchestrator prompts need tuning.

### Monthly (30–60 min)
1. Read `monthly.md`
2. Anti-pattern review: what kinds of slop is the smaller LLM consistently producing? Update the orchestrator's system prompt or recipe-policy doc.
3. RAM/perf check: did diagnosis mode hit its non-peak time budget cap? If yes, audit which incidents are eating budget.
4. Retrospective entry into the journal.

---

## Slop-mitigation principles

The smaller LLM *will* produce slop. Specifically expect:
1. **Redundant alerts** — same root issue alerted from 3 services in 2 minutes
2. **Over-eager recipes** — firing on benign signals (a brief log spike during a known cron tick)
3. **Surface-level fixes** — proposals that paper over root cause rather than fixing it
4. **Verbose, non-canonical formatting** — markdown that doesn't match the schema
5. **Hallucinated paths or function names** — citing files that don't exist or methods that have been renamed

Defenses, in order of importance:

1. **Hard gate: orchestrator never modifies non-orchestrator code directly.** Tier separation is the only real protection.
2. **Self-rate-limit:** same incident pattern (by `service`+`symptom_verbatim` hash) within 1 hour → suppress duplicate, increment counter on the original incident.
3. **Hypothesis budget:** each novel incident gets ≤3 hypothesis-test cycles before classification flips to `requires-review`. Prevents the LLM from infinite-looping a confused diagnosis.
4. **Schema-strict logging:** orchestrator's own JSONL output must validate against a JSON Schema; non-conforming entries go to a quarantine path and trigger a `requires-review`.
5. **Path-existence pre-check:** before referencing any file path in a proposal, the orchestrator must `os.path.exists()` it. Hallucinated paths blocked at write-time.
6. **Quarterly Opus retrospective:** review the month-of-monthlies, catalogue recurring anti-patterns, and update the orchestrator's system prompt or model choice.

---

## Implementation phases (when you're ready)

**Prereq (before any of this):** `Mac-mini/PLAN.md` Phase 6 complete — heartbeat, Pushover, weekly SSH audit. The orchestrator depends on the heartbeat as its system-down trigger.

**P0 — Audit log skeleton (~1 weekend)**
- `orchestrator/` directory, JSONL writer, `incidents.jsonl` schema validator
- Daily.md generator (no LLM yet)
- Wire heartbeat + recipe-fire counters into the audit
- Outcome: structured observation, no autonomy

**P1 — Triage mode + deterministic recipes (~1 weekend)**
- Encode the existing failure-mode catalogue from your `feedback_*.md` memory files as recipes:
  - `keychain_unlock`, `intake_sweep`, `error_log_watch`, `launchd_health`, `ollama_503_backoff`, `qwen3_think_false_enforcement`
- Run during peak; deterministic actions only; no LLM
- Outcome: known failures auto-recover during peak hours

**P2 — Non-peak diagnosis mode (~1–2 weekends)**
- LLM orchestrator with qwen3:14b backbone (already resident)
- Hypothesis budget, schema-strict logging, path pre-check
- Writes to `proposals/`, never applies
- Outcome: novel failures get diagnosed and proposed-fixed during quiet hours

**P3 — Peak override + system-down detection (~half weekend)**
- Heartbeat-missing / crash-loop / dashboard-down detector flips peak→diagnosis
- Synthetic outage test: kill 3 KeepAlive agents at noon, verify orchestrator switches mode and pages you
- Outcome: emergencies don't wait for non-peak

**P4 — Opus review tooling (~half weekend)**
- Helper scripts: `orchestrator review --since=yesterday` opens daily.md and lists pending proposals
- Optionally a Claude Code skill (`/orchestrator-review`) that loads the daily.md and walks you through pending items
- Outcome: review is a 5-minute habit, not a chore

**P5 — Telegram chat surface (~weekend, separable)**
- Telegram bot exposes orchestrator state read-only by default:
  - "what failed today?" → daily.md TL;DR + any requires-review
  - "drift?" → drift section
  - "approve <incident-id>" → confirms a low-risk proposal in-thread (medium/high stays Opus-only)
- Auth: bot token in keychain, allowlist of authorized chat IDs, 60s rate limit (mirror your finance-monitor pattern)
- Outcome: on-the-go visibility without browser/SSH

---

## Open questions / decisions deferred

| Question | Default | Revisit when |
|---|---|---|
| Backbone model: qwen3:14b vs hermes3:8b | qwen3:14b (already resident, no extra RAM) | After P2 if function-calling JSON quality is the bottleneck |
| Orchestrator as own LaunchAgent or extension of `service-monitor` | Own LaunchAgent — keep `service-monitor` observe-only | Probably never; clean separation pays off long-term |
| Recipe promotion threshold | Same recipe fires ≥7 days running → escalate to permanent fix | After 30 days of P2 data |
| iMessage chat surface (BlueBubbles) | No | Only if Telegram falls short — currently no evidence it will |
| Adopt a published agent framework (Hermes Agent / OpenClaw / LangGraph) | No, until provenance audited | Per `~/.claude/plans/i-am-considering-restructuring-virtual-dream.md` Phase 0 — clone, audit commit history, audit issue numbers, audit license; reject if SEO-spam pattern |
| Where does code review happen — IDE Opus session or in-Telegram approve? | IDE for non-trivial; in-Telegram only for low-risk pre-approved categories | Calibrate after P5 |

---

## Critical files / existing infrastructure to reuse

When you implement, these are your hooks:
- `service-monitor/services.py:SERVICES` — single source of truth for the agent registry; orchestrator iterates this, doesn't maintain its own list
- `service-monitor/flowchart.py` — mental model of service relationships; orchestrator's recipe targeting reads from here
- `dispatcher/router.py` + `dispatcher/slack_bot.py` — Slack Socket Mode + sidecar IPC pattern; copy for Telegram bot in P5
- `finance-monitor/query_engine.py` — proven qwen3 tool-calling pattern; the orchestrator's diagnosis loop should mirror its prompt structure
- `event-aggregator/connectors/` — connector contract pattern (`ConnectorStatus`-as-result-type); orchestrator's recipes should return an analogous `RecipeOutcome` type for uniformity
- `Mac-mini/PLAN.md` Phase 6 — heartbeat + Pushover (prereq)
- `~/.claude/projects/-Users-ianreed-Documents-GitHub-Home-Tools/memory/feedback_*.md` — source material for the initial recipe catalogue in P1

---

## Anti-goals (explicitly out of scope)

- **Orchestrator that modifies service code without Opus review.** Hard rule. The whole architecture rests on this.
- **Adopting a published agent framework on the strength of blog posts.** OpenClaw and "Hermes Agent" both look SEO-inflated. Audit-or-don't-adopt.
- **Always-resident model larger than 14B.** 24 GB ceiling won't tolerate it alongside qwen3:14b + qwen2.5vl:7b.
- **iMessage write integration via BlueBubbles.** Marginal benefit, real maintenance cost; Telegram covers the use case.
- **HTTP/RPC layer between services.** Filesystem IPC + sidecar JSON is your existing pattern; the orchestrator participates in it, doesn't replace it.
- **Auto-promotion of recipes (Tier 2 → Tier 1) without Opus signoff.** Promotion is a code change; code changes go through review.
- **Opus making decisions in production from automation.** Opus is the human-in-the-loop reviewer; if you want a fully autonomous system, you don't want Opus at all — you want a different design.

---

## One-paragraph TL;DR for future-you reading this cold

Build a Tier 2 LLM "orchestrator" service that runs alongside the existing 11 LaunchAgents on the Mac mini. It reads logs and state, runs deterministic recovery recipes during peak hours, and queues novel-failure diagnosis to non-peak hours (overriding to immediate when the system is fully down). It writes structured JSONL audit + Markdown daily/weekly/monthly changelogs, plus diff-style proposals for any code changes it suggests — but it **never applies code changes itself**. Opus 4.7, in your IDE on a daily/weekly cadence, reviews the changelogs, applies/rejects/promotes proposals, and tunes the orchestrator's prompts based on slop patterns. The architecture's load-bearing invariant is asymmetric trust: cheap-but-noisy autonomy below, high-trust review above. Prereq is finishing Phase 6 of `Mac-mini/PLAN.md` first.
