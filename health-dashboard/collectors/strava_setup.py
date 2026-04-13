"""One-time Strava OAuth setup."""
import json
import keyring
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from stravalib.client import Client

client_id = keyring.get_password("health-dashboard-strava", "client_id")
client_secret = keyring.get_password("health-dashboard-strava", "client_secret")

if not client_id or not client_secret:
    print("ERROR: Strava client_id/client_secret not in keychain. Run setup.sh first.")
    exit(1)

client = Client()
auth_url = client.authorization_url(
    client_id=int(client_id),
    redirect_uri="http://localhost:8090/callback",
    scope=["read_all", "activity:read_all"],
)

print(f"If browser doesn't open, visit:\n{auth_url}\n")
webbrowser.open(auth_url)

code = None

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global code
        code = parse_qs(urlparse(self.path).query).get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Success! You can close this tab.</h1>")
    def log_message(self, *a):
        pass

print("Waiting for Strava authorization...")
HTTPServer(("localhost", 8090), CallbackHandler).handle_request()

if code:
    r = client.exchange_code_for_token(
        client_id=int(client_id),
        client_secret=client_secret,
        code=code,
    )
    tokens = {
        "access_token": r["access_token"],
        "refresh_token": r["refresh_token"],
        "expires_at": r["expires_at"],
    }
    keyring.set_password("health-dashboard-strava", "tokens", json.dumps(tokens))
    print("Strava authorization successful!")
else:
    print("ERROR: No authorization code received.")
