"""Step-by-step progress for one answer. Streamlit is not required.

Each line has a London timestamp and the elapsed time for this answer.
The text names titles, counts and scores. It does not include policy text or secrets.
"""

import contextvars
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

_active: contextvars.ContextVar["Progress | None"] = contextvars.ContextVar(
    "nathan_progress", default=None
)
_LONDON = ZoneInfo("Europe/London")


def brief(text, limit=160) -> str:
    words = " ".join(str(text or "").split())
    if len(words) <= limit:
        return words
    return words[: limit - 1] + "…"


class Progress:
    def __init__(self, on_event: Callable | None = None, now=None):
        self.on_event = on_event
        self.lines: list[str] = []
        self.labels: list[str] = []
        self._now = now or _perf
        self.started = self._now()

    @property
    def elapsed(self) -> float:
        return max(0.0, self._now() - self.started)

    def emit(self, label: str, detail: str) -> str:
        stamp = datetime.now(_LONDON).strftime("%H:%M:%S")
        line = f"{stamp}  +{self.elapsed:.2f}s  {brief(detail, 400)}"
        self.labels.append(label)
        self.lines.append(line)
        if self.on_event:
            self.on_event((label, line))
        return line


def emit(label: str, detail: str) -> None:
    progress = _active.get()
    if progress is not None:
        progress.emit(label, detail)


@contextmanager
def use_progress(progress: Progress):
    token = _active.set(progress)
    try:
        yield progress
    finally:
        _active.reset(token)


def _perf() -> float:
    import time

    return time.perf_counter()
