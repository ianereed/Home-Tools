import os

# On the mini, homeserver's login keychain isn't reachable via the aqua
# default-keychain search path, and keyring>=25 ignores Keyring.keychain.
# When KEYCHAIN_PATH is set, shim keyring to call `security` with an
# explicit keychain path. On the laptop (var unset), keyring is unchanged.
if os.environ.get("KEYCHAIN_PATH"):
    import subprocess
    import keyring

    _kc = os.environ["KEYCHAIN_PATH"]

    # Empty-password login keychain on the mini; SSH-opened sessions auto-unlock
    # it via the audit session, but LaunchAgent contexts may not. Unlock once
    # at module import.
    subprocess.run(["security", "unlock-keychain", "-p", "", _kc], capture_output=True)

    def _get(service, username):
        # Pass the explicit keychain path: SSH audit sessions have a search list
        # of only System.keychain, so an unqualified lookup misses items stored
        # in login.keychain-db. _kc is the keychain we were told to use.
        p = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", username, "-w", _kc],
            capture_output=True, text=True,
        )
        if p.returncode != 0:
            import sys
            print(f"[keyring-shim] security rc={p.returncode} for {service}/{username}: {p.stderr.strip()!r}", file=sys.stderr)
            return None
        return p.stdout.rstrip("\n")

    def _set(service, username, password):
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", username, "-w", password, _kc],
            check=True,
        )

    def _delete(service, username):
        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", username, _kc],
            check=True,
        )

    keyring.get_password = _get
    keyring.set_password = _set
    keyring.delete_password = _delete
