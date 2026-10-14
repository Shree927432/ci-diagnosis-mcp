"""Shared types for the analysis layer. No network, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

MODES = (
    "test_assertion",
    "test_load",
    "compile",
    "lint_format",
    "dependency",
    "runtime",
    "config",
    "out_of_memory",
    "timeout",
    "cancelled",
    "infra",
    "unknown",
)

# Likelihood ratios per DESIGN.md §7.
WEIGHT_RATIO = {
    "decisive": 50.0,
    "strong": 8.0,
    "medium": 3.0,
    "weak": 1.5,
    "against": 1 / 3,
    "strong_against": 1 / 8,
}


@dataclass(slots=True)
class LogLine:
    index: int  # 0-based position in the original log
    ts: datetime | None
    text: str  # timestamp and ANSI stripped, truncated to MAX_LINE


@dataclass(slots=True)
class ExtractedError:
    message: str
    log_line: int  # 1-based line number in the original log
    file: str | None = None
    line: int | None = None
    column: int | None = None
    code: str | None = None
    rule: str | None = None
    test: str | None = None


@dataclass(slots=True)
class FailedTest:
    name: str
    log_line: int
    file: str | None = None
    message: str | None = None


@dataclass(slots=True)
class FileRef:
    path: str
    line: int | None = None
    column: int | None = None
    mentions: int = 1


@dataclass(slots=True)
class Observation:
    """One parser finding: a mode with a weight class and the log lines it claims."""

    mode: str
    weight: str  # key of WEIGHT_RATIO
    parser: str
    summary: str
    anchor: int  # 0-based index of the line the excerpt centres on
    claimed: tuple[int, int]  # 0-based inclusive range of lines this parser owns
    errors: list[ExtractedError] = field(default_factory=list)
    failed_tests: list[FailedTest] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    root_cause_hint: str = ""
    count: int = 1  # identical repeated lines collapse into one observation with a count
