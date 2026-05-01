"""
Output adapter dispatch.

Adapters are thin wrappers over existing project code (slack_notifier,
google_calendar writer, todoist writer, etc.). The Job framework only
needs to know `output_config["target"]` to route the payload.

Why route through here instead of letting Jobs call the writers directly?
The migration_verifier and Mini Ops console can introspect output_config
to reason about what a Job WILL do without executing it; adapters keep
that contract uniform.
"""
from __future__ import annotations

import logging
from typing import Any

from jobs.adapters import card, gcal, nas, sheet, slack, todoist

logger = logging.getLogger(__name__)


_ADAPTERS = {
    "slack": slack.send,
    "gcal": gcal.write_event,
    "todoist": todoist.create_task,
    "card": card.post_card,
    "nas": nas.write_file,
    "sheet": sheet.append_row,
}


def dispatch(output_config: dict, payload: dict) -> dict:
    """Send `payload` to the destination named in `output_config["target"]`.

    Returns the adapter's response dict. Raises ValueError for unknown targets.
    Each adapter is responsible for its own error handling — but failures
    propagate as exceptions so huey records them as failed Jobs.
    """
    target = output_config.get("target")
    if not target:
        raise ValueError(
            f"output_config missing 'target' key. Got keys: {sorted(output_config.keys())}. "
            "Use jobs.lib.output_config(target=..., **fields) to build it."
        )
    adapter = _ADAPTERS.get(target)
    if adapter is None:
        raise ValueError(
            f"unknown adapter target {target!r}. "
            f"Supported: {sorted(_ADAPTERS.keys())}"
        )
    return adapter(output_config, payload)


def list_targets() -> list[str]:
    return sorted(_ADAPTERS.keys())
