"""Formatter checks: prettier, black, ruff format, isort, rustfmt, gofmt."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, find_all, find_first, obs, plural

NAME = "format"
GATE = re.compile(r"""^\[warn\]|would reformat|Would reformat|would be reformatted|Oh no!|Imports are incorrectly sorted|^Diff in |gofmt|not formatted|files were modified by this hook|\.{5,}Failed$""", re.M)
RULES = [
    ("prettier", r"^\[warn\]\s+(\S+\.\w+)\s*$", r"Code style issues found|Run Prettier with --write", "npx prettier --write <files>"),
    ("black", r"^would reformat\s+(\S+)", r"would be reformatted|Oh no!", "black <files>"),
    ("ruff format", r"^Would reformat:\s+(\S+)", r"files? would be reformatted|Would reformat:", "ruff format <files>"),
    ("isort", r"^ERROR:\s+(\S+)\s+Imports are incorrectly sorted", r"Imports are incorrectly sorted", "isort <files>"),
    ("rustfmt", r"^Diff in (\S+?)(?::\d+)?(?: at line \d+)?:?$", r"^Diff in ", "cargo fmt"),
    ("gofmt", r"^(\S+\.go)$", r"gofmt -l|not gofmt'?d|files are not formatted", "gofmt -w <files>"),
]
_COMPILED = [(t, re.compile(f), re.compile(m, re.I), fix) for t, f, m, fix in RULES]
_HOOK = re.compile(r"^(\S.*?)\.{5,}Failed$")
_MODIFIED = re.compile(r"files were modified by this hook")
_HOOK_FILE = re.compile(r"^- ((?:[\w.@-]+/)*[\w.@-]+\.\w+):$|^diff --git a/(\S+) b/")


def _precommit(lines: list[LogLine]) -> list:
    hooks = find_all(lines, _HOOK)
    if not hooks:
        return []
    names = list(dict.fromkeys(m.group(1).strip() for _, m in hooks))
    modified = bool(find_first(lines, _MODIFIED))
    files = [(l, m.group(1) or m.group(2)) for l, m in find_all(lines, _HOOK_FILE)]
    errors = [ExtractedError(f"pre-commit hook '{m.group(1).strip()}' failed" + (" and modified files" if modified else ""), l.index + 1) for l, m in hooks]
    errors += [ExtractedError("Modified by pre-commit hooks", l.index + 1, f) for l, f in files]
    return [obs("lint_format", "decisive" if modified else "strong", NAME, f"pre-commit: {plural(len(names), 'hook')} failed ({', '.join(names)})", [l for l, _ in hooks] + [l for l, _ in files], anchor=hooks[0][0].index,
                errors=errors, signals=[l.text.strip() for l, _ in hooks][:5],
                hint="Run `pre-commit run --all-files` locally and commit the result." if modified else "Run `pre-commit run --all-files` locally; the hook output above names the problem.")]


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    pc = _precommit(lines)
    if pc:
        return pc
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
