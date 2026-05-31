"""One-time interactive Garmin OAuth token seeder.

Garmin enforces MFA + IP rate-limits on fresh email/password logins, which a
headless launchd/jobs context cannot satisfy. Run this ONCE, interactively, to
log in (entering the MFA code Garmin emails/texts you) and write the OAuth token
store to ``~/.garminconnect``. The collector then resumes from those tokens
silently — no MFA, no password login, no 429s. Garmin's tokens last ~1 year.

Run it on the mini with a TTY so the MFA prompt works::

    ssh -t homeserver@homeserver \
      'cd ~/Home-Tools/health-dashboard && .venv/bin/python3 -m collectors.seed_garmin_token'
"""

import os

import keyring

from garminconnect import Garmin

KEYRING_SERVICE = "health-dashboard-garmin"
TOKEN_DIR = os.path.expanduser("~/.garminconnect")


def main() -> None:
    email = keyring.get_password(KEYRING_SERVICE, "email")
    password = keyring.get_password(KEYRING_SERVICE, "password")
    if not email or not password:
        raise SystemExit(
            f"Garmin credentials not found in keychain (service "
            f"{KEYRING_SERVICE!r}, accounts 'email'/'password')."
        )

    print(f"Logging in to Garmin as {email} ...")
    client = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("Enter the Garmin MFA code: ").strip(),
    )
    client.login()  # fresh credential login; prompts for MFA if Garmin asks

    os.makedirs(TOKEN_DIR, exist_ok=True)
    client.client.dump(TOKEN_DIR)
    print(f"Success. OAuth tokens written to {TOKEN_DIR}")
    print("The collector can now resume silently. Re-run only if tokens expire (~1 year).")


if __name__ == "__main__":
    main()
