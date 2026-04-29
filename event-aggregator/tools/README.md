# iMessage export — laptop ↔ mini bridge

The Mac mini that runs `event-aggregator` is deliberately not signed into
iCloud, so its `~/Library/Messages/chat.db` doesn't exist. iMessages live on
the user's laptop. This directory holds the laptop-side scripts that ship a
JSONL of recent iMessages to the mini every 10 min.

The mini's `IMessageConnector` reads that JSONL when `IMESSAGE_EXPORT_FILE` is
set in the mini's `.env`. When the file is missing or older than
`IMESSAGE_EXPORT_MAX_AGE_MIN` (default 120), the connector reports
`permission_denied` with a stale-aware message. Parsed messages are still
returned so old-but-unseen rows can land in the pipeline.

## Files

| Path | Purpose |
|---|---|
| `imessage_export.py` | Stdlib-only Python that reads chat.db and writes a JSONL of the last 14 days. Refuses to write into iCloud-Drive paths. |
| `imessage_export.sh` | Zsh wrapper called by the LaunchAgent — runs the python, scp's the JSONL to the mini, atomic-renames on the receiver. |
| `../com.home-tools.imessage-export.plist` | LaunchAgent manifest. Runs every 600s. |

## Install (on the laptop, one-time)

1. Pull this repo to `~/Documents/GitHub/Home-Tools` (already there for any user this is being installed for).
2. Verify Tailscale SSH to the mini works without a prompt:
   ```sh
   ssh homeserver@homeserver echo ok
   ```
   If that emits a host-key-changed warning, refresh known_hosts:
   ```sh
   ssh-keygen -R homeserver
   ssh-keygen -R homeserver.local
   ssh-keygen -R 100.66.241.126
   ssh homeserver@homeserver echo ok      # accept the new key once
   ```
3. Grant Full Disk Access to the python that the LaunchAgent will run:
   - Open System Settings → Privacy & Security → Full Disk Access.
   - Click `+`, navigate to `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`.
   - macOS may resolve to `…/Resources/Python.app/Contents/MacOS/Python` — accept whatever it canonicalizes to.
4. Verify FDA propagated to launchd context:
   ```sh
   launchctl asuser $UID /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
     -c 'import pathlib; p=pathlib.Path("~/Library/Messages/chat.db").expanduser(); print(p.stat().st_size)'
   ```
   Must print a non-zero file size. If `Operation not permitted`, see the **FDA fallback** at the end of this README.
5. Install + load the LaunchAgent:
   ```sh
   mkdir -p ~/Library/LaunchAgents ~/Library/Logs/home-tools ~/imessage-export
   chmod 700 ~/imessage-export
   cp event-aggregator/com.home-tools.imessage-export.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.home-tools.imessage-export.plist
   launchctl kickstart -k gui/$UID/com.home-tools.imessage-export
   ```
6. On the mini, set the env var so the connector reads the JSONL:
   ```sh
   ssh homeserver@homeserver "
     mkdir -p ~/Home-Tools/event-aggregator/cache && chmod 700 ~/Home-Tools/event-aggregator/cache
     grep -q '^IMESSAGE_EXPORT_FILE=' ~/Home-Tools/event-aggregator/.env || \
       echo 'IMESSAGE_EXPORT_FILE=/Users/homeserver/Home-Tools/event-aggregator/cache/imessage.jsonl' >> ~/Home-Tools/event-aggregator/.env
   "
   ssh homeserver@homeserver 'launchctl kickstart -k gui/$(id -u)/com.home-tools.event-aggregator.worker'
   ```

## Verify

```sh
# Laptop
launchctl print gui/$UID/com.home-tools.imessage-export | head -20
tail ~/Library/Logs/home-tools/imessage-export.log
wc -l ~/imessage-export/imessage.jsonl

# Mini
ssh homeserver@homeserver 'wc -l ~/Home-Tools/event-aggregator/cache/imessage.jsonl'
ssh homeserver@homeserver 'cd ~/Home-Tools/event-aggregator && python3 -c "import json; print(json.load(open(\"state.json\"))[\"connector_health\"][\"imessage\"])"'
```

`state.json[connector_health][imessage][last_status_code]` should read `ok`. The service-monitor at `homeserver:8502` reflects the same.

End-to-end smoke: send yourself an obviously event-shaped iMessage from your phone like `Test: lunch tomorrow at noon at Test Cafe`. Within ~15 min, a proposal lands in `#ian-event-aggregator`.

## Troubleshooting

**Dashboard says `export file stale — N min old; check laptop launchd`.**
The laptop hasn't shipped a fresh file recently.
```sh
ssh homeserver@homeserver 'stat -f %Sm ~/Home-Tools/event-aggregator/cache/imessage.jsonl'
tail ~/Library/Logs/home-tools/imessage-export.err.log
launchctl print gui/$UID/com.home-tools.imessage-export | grep -E 'state|last exit'
```
Most likely causes: laptop closed for a while (resolves on wake), Tailscale SSH not reachable (check `tailscale status`), or scp BatchMode failing because known_hosts was wiped on the laptop.

**Dashboard says `export file missing — laptop exporter not running`.**
The mini's `IMESSAGE_EXPORT_FILE` is set but no file exists at that path. Either the LaunchAgent never ran on the laptop or the scp/mv step has been failing since install.
```sh
launchctl print gui/$UID/com.home-tools.imessage-export
launchctl kickstart -k gui/$UID/com.home-tools.imessage-export
sleep 30 && tail ~/Library/Logs/home-tools/imessage-export.err.log
```

**Dashboard says `export jsonl parse failed` or `export schema mismatch`.**
A macOS update probably moved a column in chat.db. Inspect:
```sh
sqlite3 ~/Library/Messages/chat.db '.schema message' | head -30
```
Update the SQL in `imessage_export.py` and the corresponding `_query()` in `connectors/imessage.py:88-98` to match.

## Disable

```sh
ssh homeserver@homeserver "sed -i.bak '/^IMESSAGE_EXPORT_FILE=/d' ~/Home-Tools/event-aggregator/.env"
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.home-tools.imessage-export.plist
rm ~/Library/LaunchAgents/com.home-tools.imessage-export.plist
```
The mini's connector reverts to the chat.db code path (which returns `permission_denied` since the mini has no chat.db — same baseline as before this feature).

## FDA fallback (only if the launchd-context probe in step 4 fails)

Some macOS versions don't honor the FDA grant on a bare framework binary when invoked via launchd. In that case, wrap the wrapper in a minimal `.app` bundle and grant FDA to the bundle:

```sh
mkdir -p ~/Applications/imessage-export.app/Contents/MacOS
cp event-aggregator/tools/imessage_export.sh ~/Applications/imessage-export.app/Contents/MacOS/imessage_export.sh
chmod 700 ~/Applications/imessage-export.app/Contents/MacOS/imessage_export.sh
cat > ~/Applications/imessage-export.app/Contents/Info.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>imessage_export.sh</string>
  <key>CFBundleIdentifier</key><string>com.home-tools.imessage-export</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
EOF
```

Then drag `~/Applications/imessage-export.app` into Privacy → Full Disk Access. Edit the LaunchAgent's `ProgramArguments` to point at `~/Applications/imessage-export.app/Contents/MacOS/imessage_export.sh` instead of the bare repo path. `launchctl bootout` and `bootstrap` to reload.
