"""macOS idle time detection via IOKit HID system."""
from __future__ import annotations

import subprocess


def get_idle_seconds() -> float:
    """Return seconds since last user input (keyboard/mouse/trackpad) on macOS.

    Uses ioreg to read HIDIdleTime from IOHIDSystem (nanoseconds).
    Returns 0.0 on any error (assumes user is active).
    """
    try:
        output = subprocess.check_output(
            ["ioreg", "-c", "IOHIDSystem", "-d", "4"],
            text=True, timeout=5,
        )
        for line in output.splitlines():
            if "HIDIdleTime" in line:
                ns = int(line.strip().split("=")[-1].strip())
                return ns / 1_000_000_000
    except Exception:
        pass
    return 0.0
