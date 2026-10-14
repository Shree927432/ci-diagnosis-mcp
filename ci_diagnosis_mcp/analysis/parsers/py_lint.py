"""mypy, pyright (compile), ruff, flake8, pylint (lint_format)."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, dedupe_errors, find_all, find_last, obs, plural, trunc

NAME = "py_lint"
GATE = re.compile(r"""\.pyi?:\d+(?::\d+)?:\s*(?:error:|[A-Z]{1,4}\d{2,4}\b)|\.py:\d+:\d+\s+-\s+error:|^Found \d+ errors?""", re.M)
_MYPY = re.compile(r"^((?:\.{0,2}/)?[\w./-]+\.pyi?):(\d+)(?::(\d+))?:\s*error:\s*(.*?)(?:\s+\[([\w-]+)\])?\s*$")
_MYPY_FOUND = re.compile(r"^Found (\d+) errors? in (\d+) files?")
_PYRIGHT = re.compile(r"^\s*((?:\.{0,2}/)?[\w./-]+\.pyi?):(\d+):(\d+)\s+-\s+error:\s*(.*?)(?:\s+\((report\w+)\))?\s*$")
_PYRIGHT_SUMMARY = re.compile(r"^(\d+) errors?, (\d+) warnings?")
_RUFF = re.compile(r"^((?:\.{0,2}/)?[\w./-]+\.pyi?):(\d+):(\d+):\s*([A-Z]{1,4}\d{2,4})\s*(?:\[\*\]\s*)?(.*)$")
_RUFF_FOUND = re.compile(r"^Found (\d+) errors?\.?")
_PYLINT = re.compile(r"^((?:\.{0,2}/)?[\w./-]+\.py):(\d+):(\d+):\s*([CRWEF]\d{4}):\s*(.*?)\s*\(([\w-]+)\)\s*$")


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    out = []
    mypy, pyright = find_all(lines, _MYPY), find_all(lines, _PYRIGHT)
    if mypy or pyright:
        hits, tool = (mypy, "mypy") if mypy else (pyright, "pyright")
        errors = [ExtractedError(trunc(m.group(4)), l.index + 1, m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None, m.group(5)) for l, m in hits]
        found = find_last(lines, _MYPY_FOUND if mypy else _PYRIGHT_SUMMARY)
        errors = dedupe_errors(errors)
        n = int(found[1].group(1)) if found else len(errors)
        out.append(obs("compile", "decisive", NAME, f"{tool}: {plural(n, 'type error')} in {len({e.file for e in errors})} file(s)", [l for l, _ in hits] + ([found[0]] if found else []),
                       errors=errors, signals=[found[0].text.strip()] if found else [], hint="Static type check failed; fix the annotated file:line, the bracketed code names the rule."))
    pylint = find_all(lines, _PYLINT)
    ruff = [(l, m) for l, m in find_all(lines, _RUFF) if not _PYLINT.match(l.text)]
    if ruff or pylint:
        tool, hits = ("pylint", pylint) if pylint and not ruff else ("ruff", ruff)
        errors = [ExtractedError(trunc(m.group(5)), l.index + 1, m.group(1), int(m.group(2)), int(m.group(3)), m.group(4), rule=m.group(6) if tool == "pylint" else None) for l, m in hits]
        found = find_last(lines, _RUFF_FOUND)
        errors = dedupe_errors(errors)
        n = int(found[1].group(1)) if found else len(errors)
        codes = ", ".join(dict.fromkeys(e.code for e in errors if e.code))[:60]
        fixable = any("[*]" in l.text or "--fix" in l.text for l in lines)
        out.append(obs("lint_format", "decisive", NAME, f"{tool}: {plural(n, 'lint error')} ({codes})", [l for l, _ in hits] + ([found[0]] if found else []),
                       errors=errors, signals=[found[0].text.strip()] if found else [], hint="Lint failed; some rules are auto-fixable with `ruff check --fix`." if fixable else "Lint failed; edit the listed file:line for each code."))
    return out
