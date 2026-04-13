#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Health Dashboard Setup ==="
echo ""

# 1. Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

echo "Installing dependencies..."
source venv/bin/activate
pip install -q -r requirements.txt

# 2. Initialize database
echo ""
echo "Initializing database..."
python3 -m collectors.db

# 3. Garmin credentials
echo ""
echo "=== Garmin Connect Setup ==="
echo "Your credentials are stored securely in macOS Keychain."
read -p "Garmin email: " garmin_email
read -sp "Garmin password: " garmin_pass
echo ""

python3 -c "
import keyring
keyring.set_password('health-dashboard-garmin', 'email', '$garmin_email')
keyring.set_password('health-dashboard-garmin', 'password', '$garmin_pass')
print('Garmin credentials saved to Keychain.')
"

# Test Garmin login
echo "Testing Garmin login..."
python3 -c "
from garminconnect import Garmin
import keyring
email = keyring.get_password('health-dashboard-garmin', 'email')
password = keyring.get_password('health-dashboard-garmin', 'password')
client = Garmin(email=email, password=password)
client.login(tokenstore='~/.garminconnect')
print('Garmin login successful!')
" || echo "WARNING: Garmin login failed. You may need MFA or check credentials."

# 4. Strava OAuth setup
echo ""
echo "=== Strava API Setup ==="
echo "You need a Strava API Application. Create one at:"
echo "  https://www.strava.com/settings/api"
echo ""
echo "Set the 'Authorization Callback Domain' to: localhost"
echo ""
read -p "Strava Client ID: " strava_client_id
read -sp "Strava Client Secret: " strava_client_secret
echo ""

python3 -c "
import keyring
keyring.set_password('health-dashboard-strava', 'client_id', '$strava_client_id')
keyring.set_password('health-dashboard-strava', 'client_secret', '$strava_client_secret')
print('Strava client credentials saved to Keychain.')
"

# Run OAuth flow
echo ""
echo "Opening browser for Strava authorization..."
python3 << 'PYEOF'
import json
import keyring
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from stravalib.client import Client

client_id = keyring.get_password("health-dashboard-strava", "client_id")
client_secret = keyring.get_password("health-dashboard-strava", "client_secret")

client = Client()
auth_url = client.authorization_url(
    client_id=int(client_id),
    redirect_uri="http://localhost:8090/callback",
    scope=["read_all", "activity:read_all"],
)

print(f"If browser doesn't open, visit:\n{auth_url}\n")
webbrowser.open(auth_url)

# Simple HTTP server to capture the callback
code = None

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global code
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Success! You can close this tab.</h1>")

    def log_message(self, format, *args):
        pass  # Suppress server logs

server = HTTPServer(("localhost", 8090), CallbackHandler)
print("Waiting for Strava authorization...")
server.handle_request()

if code:
    token_response = client.exchange_code_for_token(
        client_id=int(client_id),
        client_secret=client_secret,
        code=code,
    )
    # token_response may be a dict or an object depending on stravalib version
    if isinstance(token_response, dict):
        tokens = {
            "access_token": token_response["access_token"],
            "refresh_token": token_response["refresh_token"],
            "expires_at": token_response["expires_at"],
        }
    else:
        tokens = {
            "access_token": token_response.access_token,
            "refresh_token": token_response.refresh_token,
            "expires_at": token_response.expires_at,
        }
    keyring.set_password("health-dashboard-strava", "tokens", json.dumps(tokens))
    print("Strava authorization successful!")
else:
    print("ERROR: No authorization code received.")
    exit(1)
PYEOF

# 5. Install launchd job
echo ""
echo "=== Setting up daily data collection ==="
PLIST_SRC="$SCRIPT_DIR/config/com.health-dashboard.collect.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.health-dashboard.collect.plist"

if [ -f "$PLIST_SRC" ]; then
    # Update paths in plist
    sed "s|__PROJECT_DIR__|$SCRIPT_DIR|g" "$PLIST_SRC" > "$PLIST_DST"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load "$PLIST_DST"
    echo "Daily collection scheduled (8:00 AM)."
else
    echo "WARNING: Plist template not found. Skipping launchd setup."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To collect data now:  cd $SCRIPT_DIR && source venv/bin/activate && python -m collectors.collect_all"
echo "To start dashboard:   $SCRIPT_DIR/run.sh"
echo ""
echo "Don't forget to set up the iOS Shortcut for Apple Health data!"
echo "See: SHORTCUT_INSTRUCTIONS.md"
