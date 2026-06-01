# /ship - Full deploy workflow

## Steps

1. **Run tests** — `pytest` from the relevant project dir. Abort if anything fails; fix first.
2. **Commit** — stage changed files by name (not `git add .`), conventional commit message, no skipping hooks.
3. **Push** — push to `main`.
4. **Deploy to mini** — `ssh homeserver@homeserver` then `git -C ~/Home-Tools pull`. Restart the relevant `com.home-tools.*` launchd agent with `launchctl kickstart -kp gui/$(id -u)/<label>`.
5. **Verify live** — tail the agent's stderr log for ~15 seconds; confirm no crash or error lines.
6. **Journal** — append a dated entry to the current `journal-N.md` at the repo root summarizing what shipped and any deploy notes.

## Notes

- If the user doesn't name the launchd label, infer it from which project changed (e.g. `event-aggregator` → `com.home-tools.event-aggregator`). Ask if ambiguous.
- Only restart agents that are actually affected by the changes.
- Don't push if tests fail — fix the failures first and confirm with the user if the fix is non-trivial.
