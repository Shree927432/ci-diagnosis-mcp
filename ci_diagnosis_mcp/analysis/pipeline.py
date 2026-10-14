"""normalize -> slice -> parse -> mode -> excerpt. Pure; used by analyze_log and the investigation layer."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .files import collect_file_refs
from .mode import ModeResult, aggregate, run_parsers
from .normalize import candidate_lines, excerpt, exit_code, parse_lines, parse_workflow_command
from .parsers._util import ParseContext
from .slicing import Slice, parse_steps, slice_to_failed_step
from .types import ExtractedError, FailedTest, FileRef, LogLine

WEIGHT_RANK = {"decisive": 3, "strong": 2, "medium": 1, "weak": 0}
_RUN = re.compile(r"^(?:##\[group\]|── )Run (.+)$")
MAX_LOG_LINES = 20000
MAX_LOG_BYTES = 20 * 1024 * 1024


@dataclass(slots=True)
class Analysis:
    mode: str
    mode_confidence: str
    mode_runner_up: str | None
    mode_margin: float
    summary: str
    root_cause_hint: str
    parser: str | None
    slice_window: str
    slice_mismatch: bool
    failed_step: str | None
    failing_command: str | None
    exit_code: int | None
    errors: list[ExtractedError]
    failed_tests: list[FailedTest]
    files: list[FileRef]
    log_excerpt: str
    signals: list[str]
    truncated: bool = False
    total_lines: int = 0
    result: ModeResult = field(default_factory=ModeResult, repr=False)


def analyze(raw: str, job: dict | None = None, *, max_excerpt_chars: int = 6000, step_name: str | None = None) -> Analysis:
    truncated = len(raw) > MAX_LOG_BYTES  # FR-2: over 20 MB keep the last 20000 lines
    lines = parse_lines(raw)
    if truncated:
        lines = lines[-MAX_LOG_LINES:]
    steps = parse_steps(job) if job else []
    sl: Slice = slice_to_failed_step(lines, steps) if steps else Slice(lines, "whole_log", None)
    ctx = ParseContext(step_name or (sl.failed_step.name if sl.failed_step else None))
    slice_res = aggregate(run_parsers(candidate_lines(sl.lines), ctx), ctx)
    res, mismatch = slice_res, False
    if sl.window != "whole_log":
        whole = aggregate(run_parsers(candidate_lines(lines), ctx), ctx)
        if whole.winner and (slice_res.winner is None or WEIGHT_RANK[whole.winner.weight] > WEIGHT_RANK[slice_res.winner.weight]) and whole.mode != slice_res.mode:
            res, mismatch = whole, True
    w = res.winner
    anchor = w.anchor if w else (sl.lines[-1].index if sl.lines else 0)
    errors = list(w.errors) if w else []
    flagged = {e.log_line - 1 for e in errors} | {l.index for l in sl.lines if parse_workflow_command(l.text) and parse_workflow_command(l.text).kind == "error"}
    region = [l for l in lines if anchor - 20 <= l.index <= anchor + 60]
    code = next((c for c in (exit_code(l.text) for l in reversed(sl.lines)) if c is not None), None)
    cmd = next((m.group(1) for l in reversed(lines) if l.index <= anchor for m in [_RUN.match(l.text)] if m), None)
    return Analysis(res.mode, res.confidence, res.runner_up, res.margin, w.summary if w else "No failure signature found", w.root_cause_hint if w else "", w.parser if w else None,
                    sl.window, mismatch, sl.failed_step.name if sl.failed_step else None, cmd, code, errors[:30], list(w.failed_tests)[:50] if w else [],
                    collect_file_refs(region, errors), excerpt(lines, anchor, flagged, max_chars=max_excerpt_chars), list(w.signals) if w else [], truncated, len(lines), res)
