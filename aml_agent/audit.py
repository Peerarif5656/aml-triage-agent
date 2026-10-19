"""Append-only audit trail for each investigation.

Every agent step, tool call, control decision and analyst action is recorded
with a timestamp so that the case can be reconstructed later (record keeping
and model risk management both require this).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable


class AuditTrail:
    def __init__(self, alert_id: str, mode: str, model: str | None, prompt_version: str, data_version: str,
                 on_event: Callable[[dict], None] | None = None):
        self.run_id = f"RUN-{uuid.uuid4().hex[:10].upper()}"
        self.alert_id = alert_id
        self.mode = mode
        self.model = model
        self.prompt_version = prompt_version
        self.data_version = data_version
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.events: list[dict] = []
        self._t0 = time.perf_counter()
        self._on_event = on_event

    def log(self, actor: str, action: str, detail=None, duration_ms: float | None = None) -> dict:
        event = {
            "seq": len(self.events) + 1,
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "elapsed_ms": round((time.perf_counter() - self._t0) * 1000, 1),
            "actor": actor,
            "action": action,
            "detail": detail,
        }
        if duration_ms is not None:
            event["duration_ms"] = round(duration_ms, 1)
        self.events.append(event)
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:  # a UI callback must never break an investigation
                pass
        return event

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "alert_id": self.alert_id,
            "mode": self.mode,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "data_version": self.data_version,
            "started_at": self.started_at,
            "events": self.events,
        }
