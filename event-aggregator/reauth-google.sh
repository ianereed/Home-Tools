#!/usr/bin/env bash
# Re-authenticate Gmail + GCal OAuth tokens on the laptop, then push to the mini.
#
# Why a separate script: the Mac mini is headless and `flow.run_local_server`
# blocks on a browser callback. The laptop has a browser, so we run OAuth
# here and copy the resulting token JSONs over.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="${TMPDIR:-/tmp}/oauth-reauth-venv"
CRED_DIR="$HERE/credentials"
CLIENT_SECRETS="$CRED_DIR/gmail_oauth.json"

if [[ ! -f "$CLIENT_SECRETS" ]]; then
  echo "ERROR: missing $CLIENT_SECRETS" >&2
  exit 1
fi

echo "==> Building disposable venv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q google-auth-oauthlib

echo "==> Running OAuth flows (browser will open twice — once for Gmail, once for GCal)"
"$VENV/bin/python" - "$CLIENT_SECRETS" "$CRED_DIR" <<'PY'
import json, sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

client_secrets = sys.argv[1]
cred_dir = Path(sys.argv[2])

jobs = [
    ("Gmail", "gmail_token.json", ["https://www.googleapis.com/auth/gmail.readonly"]),
    ("GCal",  "gcal_token.json",  ["https://www.googleapis.com/auth/calendar.events"]),
]

for label, fname, scopes in jobs:
    print(f"\n--- {label} ---")
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, scopes)
    creds = flow.run_local_server(port=0, prompt="consent")
    out = cred_dir / fname
    out.write_text(creds.to_json())
    out.chmod(0o600)
    print(f"  wrote {out}")
PY

echo
echo "==> Tokens refreshed. Pushing to mini..."
scp "$CRED_DIR/gmail_token.json" "$CRED_DIR/gcal_token.json" \
    homeserver@homeserver:Home-Tools/event-aggregator/credentials/

echo
echo "==> Done. Watch the next fetch tick:"
echo "  ssh homeserver@homeserver 'tail -f ~/Library/Logs/home-tools/event-aggregator-fetch.log'"
