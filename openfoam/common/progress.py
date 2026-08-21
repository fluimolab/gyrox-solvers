"""Registered progress-event NDJSON writer with no raw-line fallback."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .schema_adapter import validate


REGISTERED_TYPES = frozenset({"progress", "residual", "checkpoint", "log", "phase"})


class ProgressWriter:
    def __init__(self) -> None:
        self._seq = 0

    def emit(self, event_type: str, payload: dict[str, Any], **envelope_fields: Any) -> dict[str, Any]:
        if event_type not in REGISTERED_TYPES:
            raise ValueError(f"unregistered progress type: {event_type}")
        self._seq += 1
        event = {
            "v": 1,
            "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "type": event_type,
            "payload": payload,
            **envelope_fields,
        }
        validate("progress-event", event)
        print(json.dumps(event, separators=(",", ":"), ensure_ascii=False), file=sys.stdout, flush=True)
        return event
