# Phase 5b — Port health-dashboard (DONE 2026-04-22)

Health-dashboard is live on the mini. Receiver on port 8095, collect at
7:00/7:20, intervals-poll every 5 min, staleness at 7am/9pm. iPhone posts
to `http://homeserver:8095/` over Tailscale. Laptop plists renamed to
`*.plist.disabled` so they don't auto-load. Records kept below for future
reference / if we ever port a similar project.

## Gotchas encountered during the port

- **Login keychain not reachable from LaunchAgents.** `homeserver`'s aqua
  session on this headless mini never got the interactive login that
  auto-unlocks the default keychain. Symptoms: `keyring.get_password`
  returns `errSecAuthFailed` (security CLI exit 152) from within a
  LaunchAgent, even though it works from an SSH shell and even though the
  keychain *is* in the search list. Fix: recreate `login.keychain-db` with
  empty password (`security create-keychain -p ""`), set no-auto-lock, and
  have the shim in `collectors/__init__.py` explicitly unlock it on import.
- **keyring>=25 ignores `Keyring.keychain`.** Even after fixing unlock,
  `keyring` can't be pointed at a specific keychain any more (upstream
  issue #623). The shim works around this by monkey-patching
  `keyring.get_password` to shell out to `security` with `KEYCHAIN_PATH`.
- **Keychain migration needed explicit target.** `security add-generic-password`
  from SSH writes to `System.keychain` (root-only → "Write permissions
  error") unless you pass the target keychain as the final positional arg.
  The same is true on the mini; the default-keychain `-d user` setting
  exists in the user preference domain but doesn't propagate to the
  Security framework calls from ssh.

## Why it wasn't trivial

- Health-dashboard ships **4 plists** (collect, intervals-poll, receiver,
  staleness), not 1. All must install cleanly.
- May have its own `requirements.txt` + credential files; treat it like a
  fresh project, not a quick re-run of event-aggregator.

## Steps (execute on the mini via SSH)

1. **Sanity-read the existing memory and code**:
   ```bash
   ssh homeserver@homeserver '
     ls ~/Home-Tools/health-dashboard/
     cat ~/Home-Tools/health-dashboard/README.md 2>/dev/null || true
     ls ~/Home-Tools/health-dashboard/config/
   '
   ```
   Read `project_health_dashboard.md` memory before proceeding.

2. **Path-cleanup check** (plists were part of the earlier sed sweep, but
   verify nothing in health-dashboard still references the wrong paths):
   ```bash
   grep -r '/Users/homeserver/Documents/GitHub' ~/Home-Tools/health-dashboard 2>/dev/null
   grep -r '/Users/ianreed' ~/Home-Tools/health-dashboard 2>/dev/null
   # Both should return nothing.
   ```

3. **Clear any stale bytecode** (the earlier sed corrupted `.pyc` files —
   this will have done the same to health-dashboard):
   ```bash
   find ~/Home-Tools/health-dashboard -type d -name __pycache__ -exec rm -rf {} +
   ```

4. **Build the venv**:
   ```bash
   cd ~/Home-Tools/health-dashboard
   uv venv --python 3.12
   source .venv/bin/activate
   uv pip install -r requirements.txt
   ```

5. **Migrate credentials / .env from laptop** (same scp pattern as
   event-aggregator, only what's needed):
   ```bash
   # FROM laptop:
   cd ~/Documents/GitHub/Home-Tools/health-dashboard
   ls .env credentials/ 2>/dev/null   # see what exists
   scp .env homeserver@homeserver:~/Home-Tools/health-dashboard/
   scp -r credentials homeserver@homeserver:~/Home-Tools/health-dashboard/ 2>/dev/null || true
   # Back on mini:
   ssh homeserver@homeserver 'cd ~/Home-Tools/health-dashboard && chmod 600 .env credentials/*.json 2>/dev/null'
   ```

6. **Smoke-test on the mini via SSH shell** (not launchd):
   ```bash
   cd ~/Home-Tools/health-dashboard
   source .venv/bin/activate
   python -c "import main" || python -m py_compile *.py  # adapt to actual entrypoint
   # Run whatever equivalent of --mock/--dry-run exists (may differ from event-aggregator).
   ```
   If there's no mock mode, skip the smoke test and trust the LaunchAgent
   install step.

7. **Apply the outstanding fixes** noted in `project_health_dashboard.md`
   memory (3 remaining steps). Resolve those before loading any plist —
   they're the reason this project hasn't been running already.

8. **Install the LaunchAgents** (4 of them). Health-dashboard may or may not
   ship an `install_scheduler.sh`. If it does, activate the venv first and
   run it. If not, copy the plist files to `~/Library/LaunchAgents/` and
   `launchctl load` each one, rewriting the Python path to
   `<project>/.venv/bin/python3`.

9. **Verify**:
   ```bash
   launchctl list | grep health-dashboard
   ls -la /tmp/home-tools-health-dashboard*.log
   ```
   PID `-` + exit status `0` + nonzero log sizes after first fire = success.

## Known gotchas to watch for

- **Empty log + Python `S` state for minutes** → TCC hang. Move whatever
  path is blocked out of `~/Documents`, `~/Downloads`, `~/Desktop`, etc.
  (Shouldn't happen since we're already at `~/Home-Tools`, but stay alert
  for any code that writes to `~/Documents/whatever`.)
- **`bad marshal data` on import** → stale `.pyc` from the earlier sed pass.
  `find ... -name __pycache__ -exec rm -rf {} +`.
- **`launchctl list` shows non-zero exit status** → always read the error
  log first. It may be Python logging at INFO (stderr by default) — cosmetic
  — or actual traceback.

## Skip meal-planner

Meal-planner is Google Apps Script + Gemini cloud. Nothing to run on the
mini. Drop from Phase 5 scope; the laptop will continue to deploy Apps
Script updates.
