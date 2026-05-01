# Phase 5d — NAS mount + LAN recovery (DONE 2026-04-29)

`/Volumes/Share1` SMB mount lost after a force-reboot triggered cascading TCC
denials, blocking ALL outbound LAN traffic from the mini. Two GUI toggles
fixed it:

1. System Settings → Privacy & Security → **Local Network** → toggle
   `tailscaled` ON. (This was the LAN-traffic blocker. With tailscaled
   denied Local Network privacy, all `192.168.4.0/22` outbound traffic
   returned `No route to host` despite valid ARP.)
2. System Settings → Privacy & Security → **Full Disk Access** → toggle
   `python3.12` ON (also `siriactionsd`, `sshd-keygen-wrapper`,
   `tailscaled`). (This was needed for launchd-spawned Python to read/write
   the SMB-mounted volume; without it, `os.listdir()` on the mount returned
   `PermissionError: Operation not permitted`.)

NAS now mounts cleanly at `~/Share1` (NOT `/Volumes/Share1` — see
`feedback_macos_volumes_dir_persistence.md`) via:

```
mount_smbfs "//iananny:${NAS_PASSWORD}@192.168.4.39/Share1" ~/Share1
```

Credentials live in `~/Home-Tools/event-aggregator/.env` under keys
`NAS_USER`, `NAS_PASSWORD`, `NAS_DHCP_IPADDRESS` (192.168.4.39),
`NAS_SSH_PORT`. Symptom signature + diagnostic ladder + recovery commands
captured in memory file `feedback_macos_lan_wedge_recovery.md`.

Helper script: `Mac-mini/scripts/mount-nas.sh` — idempotent SMB remount
using credentials from `event-aggregator/.env`. Run after every reboot
(SMB mounts don't survive reboot on macOS) until autofs is set up. Reboot
persistence verified 2026-04-29: TCC grants persist; only the mount itself
needs re-issuing.

Two follow-ups identified during the security audit (not addressed):
- sshd `PasswordAuthentication yes` should be `no` for defense in depth.
- smbd is listening on `*:445` (mini is acting as an SMB server). Confirm
  intent; toggle off File Sharing if unintentional.
