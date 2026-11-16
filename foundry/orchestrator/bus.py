"""The tower's nervous system: events in, handlers out, every event written down once.

    Event   one thing that happened: a type, a key, a detail, who sent it, what caused it
    Bus     publish() queues an event unless its key was seen before (idempotency: a cron that
            fires twice, a carrier feed that repeats itself, a person who double-clicks Approve);
            on() subscribes a handler to a type; drain() runs the queue first-in first-out
    Journal one line per event and outcome: ok, duplicate, no handler, or dead (a handler raised:
            the event goes to the dead-letter list with the error and the rest of the run goes on)

EventBridge delivers at least once, SNS delivers at least once, people click twice: the key is how
the tower stays calm about all three. Same idea as a cell that ignores a second dose of a hormone
while its receptors are still occupied.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Event:
    id: str
    type: str
    key: str
    detail: dict
    source: str = "tower"
    at: float = 0.0                       # the tower's clock: hours since Monday 06:30
    caused_by: str | None = None
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return asdict(self)


class Bus:
    def __init__(self, journal_path: str | Path | None = None, clock: Callable[[], float] | None = None,
                 max_events: int = 500):
        self.handlers: dict[str, list[tuple[str, Callable[[Event], None]]]] = {}
        self.queue: deque[Event] = deque()
        self.seen: set[str] = set()
        self.journal: list[dict] = []
        self.dead: list[dict] = []
        self.path = Path(journal_path) if journal_path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: 0.0)
        self.max_events = max_events
        self._current: Event | None = None
        self._n = 0

    # --- wiring -------------------------------------------------------------------------------
    def on(self, type_: str, handler: Callable[[Event], None], name: str | None = None) -> None:
        self.handlers.setdefault(type_, []).append((name or handler.__name__.lstrip("_"), handler))

    # --- sending --------------------------------------------------------------------------------
    def publish(self, type_: str, detail: dict | None = None, key: str | None = None, source: str | None = None) -> Event | None:
        key = key or f"{type_}:{uuid.uuid4().hex[:8]}"
        caused_by = self._current.id if self._current else None
        if key in self.seen:
            self._write({"type": type_, "key": key, "status": "duplicate", "caused_by": caused_by,
                         "note": "this key was seen before; ignored (idempotent)"})
            return None
        self.seen.add(key)
        ev = Event(id=f"EV-{len(self.seen):04d}", type=type_, key=key, detail=detail or {},
                   source=source or (self._current.source if self._current else "tower"), at=self.clock(), caused_by=caused_by)
        self.queue.append(ev)
        return ev

    # --- running --------------------------------------------------------------------------------
    def drain(self) -> list[dict]:
        """Run every queued event, and the events they cause, until the queue is empty."""
        done = []
        while self.queue:
            if self._n >= self.max_events:
                self._write({"type": "bus", "key": "max_events", "status": "stopped",
                             "note": f"stopped after {self.max_events} events: a handler is probably re-publishing itself"})
                self.queue.clear()
                break
            ev = self.queue.popleft()
            self._n += 1
            handlers = self.handlers.get(ev.type, [])
            row = {"id": ev.id, "type": ev.type, "key": ev.key, "at": ev.at, "source": ev.source, "caused_by": ev.caused_by,
                   "detail": _short(ev.detail), "handled_by": [h for h, _ in handlers], "status": "ok" if handlers else "no_handler"}
            self._current = ev
            for name, fn in handlers:
                try:
                    note = fn(ev)
                    if note:
                        row.setdefault("notes", []).append(f"{name}: {note}")
                except Exception as e:        # a broken handler must not stop the tower
                    row["status"] = "dead"
                    self.dead.append({"event": ev.as_dict(), "handler": name, "error": f"{type(e).__name__}: {e}"})
                    row.setdefault("notes", []).append(f"{name} raised {type(e).__name__}: {e}")
            self._current = None
            self._write(row)
            done.append(row)
        return done

    def _write(self, row: dict) -> None:
        self.journal.append(row)
        if self.path:
            with open(self.path, "a") as f:
                f.write(json.dumps(row, default=str) + "\n")

    def trace(self, event_id: str) -> list[dict]:
        """The chain of events that led to this one (newest last): the answer to "why did the tower do that?"."""
        by_id = {r["id"]: r for r in self.journal if "id" in r}
        chain, cur = [], by_id.get(event_id)
        while cur:
            chain.append(cur)
            cur = by_id.get(cur.get("caused_by"))
        return list(reversed(chain))


def _short(detail: dict, limit: int = 400) -> dict:
    s = json.dumps(detail, default=str)
    if len(s) <= limit:
        return detail
    return {k: (v if len(json.dumps(v, default=str)) < 120 else "...") for k, v in detail.items()}
