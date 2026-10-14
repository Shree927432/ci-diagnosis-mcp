"""Locate the failed step's lines by timestamp window, verify, widen, or fall back to the whole log (FR-4)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .normalize import is_error_line
from .types import LogLine

FAILED = ("failure", "timed_out", "cancelled")
SLACK = timedelta(seconds=1.5)


@dataclass(slots=True)
class Step:
    name: str
    number: int
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(slots=True)
class Slice:
    lines: list[LogLine]
    window: str  # failed_step | widened | whole_log
    failed_step: Step | None


def failed_step(steps: list[Step]) -> Step | None:
    """First failed step; cancelled counts only when nothing else failed."""
    for s in steps:
        if s.conclusion in ("failure", "timed_out"):
            return s
    return next((s for s in steps if s.conclusion == "cancelled"), None)


def _window(lines: list[LogLine], start: datetime, end: datetime) -> list[LogLine]:
    lo, hi = start - SLACK, end + SLACK
    out, inside = [], False
    for l in lines:
        if l.ts is not None:
            inside = lo <= l.ts <= hi
        if inside:
            out.append(l)
    return out


def _verified(lines: list[LogLine]) -> bool:
    return len(lines) >= 2 and any(is_error_line(l.text) for l in lines)


def slice_to_failed_step(lines: list[LogLine], steps: list[Step]) -> Slice:
    step = failed_step(steps)
    if step is None or step.started_at is None or step.completed_at is None:
        return Slice(lines, "whole_log", step)
    sliced = _window(lines, step.started_at, step.completed_at)
    if _verified(sliced):
        return Slice(sliced, "failed_step", step)
    prev = [s for s in steps if s.number < step.number and s.started_at is not None]
    if prev:
        widened = _window(lines, prev[-1].started_at, step.completed_at)  # type: ignore[arg-type]
        if _verified(widened):
            return Slice(widened, "widened", step)
    return Slice(lines, "whole_log", step)


def parse_steps(job: dict) -> list[Step]:
    """Build Steps from a GitHub job JSON object (ISO timestamps, 'Z' suffix)."""
    out = []
    for s in job.get("steps") or []:
        out.append(Step(s.get("name", ""), int(s.get("number", 0)), s.get("conclusion"), _iso(s.get("started_at")), _iso(s.get("completed_at"))))
    return out


def _iso(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
