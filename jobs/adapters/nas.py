"""
NAS adapter — writes a file under ~/nas/ (Tailscale-mounted Synology share).
Used by Jobs that produce documents (digests, reports, exports).
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

NAS_ROOT = Path.home() / "nas"


def write_file(output_config: dict, payload: dict) -> dict:
    """Write `payload['content']` to `<NAS_ROOT>/output_config['relpath']`.

    output_config:
        target: "nas"
        relpath: "Reports/2026/digest.md"  (relative to ~/nas/; required)
    payload:
        content (str | bytes)              — file contents
        mode (str, default "w")            — "w" or "wb"
    """
    relpath = output_config.get("relpath")
    if not relpath:
        raise ValueError("nas adapter: output_config missing 'relpath'")
    if relpath.startswith("/") or ".." in Path(relpath).parts:
        raise ValueError(f"nas adapter: relpath must be relative + non-traversing: {relpath!r}")
    if not NAS_ROOT.exists():
        raise RuntimeError(
            f"nas adapter: {NAS_ROOT} does not exist. "
            "On the mini, autofs mounts the Synology share — verify with `ls ~/nas/`."
        )

    target = NAS_ROOT / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = payload.get("mode", "w")
    content = payload.get("content", "")
    if mode == "wb":
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
    else:
        target.write_text(content if isinstance(content, str) else content.decode())
    return {"path": str(target), "bytes": target.stat().st_size}
