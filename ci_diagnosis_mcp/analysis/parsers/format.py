"""Formatter checks: prettier, black, ruff format, isort, rustfmt, gofmt."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, find_all, find_first, obs, plural

NAME = "format"
GATE = re.compile(r"""^\[warn\]|would reformat|Would reformat|would be reformatted|Oh no!|Imports are incorrectly sorted|^Diff in |gofmt|not formatted""", re.M)
RULES = [
    ("prettier", r"^\[warn\]\s+(\S+\.\w+)\s*$", r"Code style issues found|Run Prettier with --write", "npx prettier --write <files>"),
    ("black", r"^would reformat\s+(\S+)", r"would be reformatted|Oh no!", "black <files>"),
    ("ruff format", r"^Would reformat:\s+(\S+)", r"files? would be reformatted|Would reformat:", "ruff format <files>"),
    ("isort", r"^ERROR:\s+(\S+)\s+Imports are incorrectly sorted", r"Imports are incorrectly sorted", "isort <files>"),
    ("rustfmt", r"^Diff in (\S+?)(?::\d+)?(?: at line \d+)?:?$", r"^Diff in ", "cargo fmt"),
    ("gofmt", r"^(\S+\.go)$", r"gofmt -l|not gofmt'?d|files are not formatted", "gofmt -w <files>"),
]
_COMPILED = [(t, re.compile(f), re.compile(m, re.I), fix) for t, f, m, fix in RULES]


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    for tool, file_re, marker_re, fix in _COMPILED:
        marker = find_first(lines, marker_re)
        if not marker:
            continue
        files = find_all(lines, file_re)
        if not files and tool not in ("prettier", "black"):
            continue
        errors = [ExtractedError(f"Not formatted ({tool})", l.index + 1, m.group(1)) for l, m in files]
        return [obs("lint_format", "decisive", NAME, f"{tool}: {plural(len(files), 'file')} not formatted", [l for l, _ in files] + [marker[0]], anchor=(files[0][0].index if files else marker[0].index),
                    errors=errors, signals=[marker[0].text.strip()], hint=f"Formatting check failed. Run `{fix}` locally and commit; no logic change needed.")]
    return []
