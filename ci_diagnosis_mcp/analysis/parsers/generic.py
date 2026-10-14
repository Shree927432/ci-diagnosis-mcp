"""Fallback: ##[error] lines, exit code, and error-looking lines. Always mode unknown, weight weak."""
from __future__ import annotations

import re

from ..normalize import exit_code, parse_workflow_command
from ..types import ExtractedError, LogLine
from ._util import ParseContext, find_last, obs, trunc

NAME = "generic"
GATE = re.compile(r""".""", re.M)
_ERRORISH = re.compile(r"^\s*(?:\[ERROR\]|ERROR|Error|error|FAILED|FAIL|fatal|FATAL|Exception|Uncaught)\b[:\]]?\s*(.+)$")
_EXIT = re.compile(r"Process completed with exit code (\d+)")


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    errors: list[ExtractedError] = []
    hits: list[LogLine] = []
    for l in lines:
        cmd = parse_workflow_command(l.text)
        if cmd and cmd.kind == "error" and cmd.message and exit_code(cmd.message) is None:
            errors.append(ExtractedError(trunc(cmd.message), l.index + 1, cmd.file, cmd.line, cmd.column))
            hits.append(l)
    exit_hit = find_last(lines, _EXIT)
    if not errors:
        cands = [l for l in lines if _ERRORISH.match(l.text) and not l.text.startswith("##[") and len(l.text) < 400]
        if exit_hit:
            cands = [l for l in cands if l.index < exit_hit[0].index]
        for l in cands[-5:]:
            errors.append(ExtractedError(trunc(l.text.strip()), l.index + 1))
            hits.append(l)
    if exit_hit:
        hits.append(exit_hit[0])
    if not hits:
        return []
    code = int(exit_hit[1].group(1)) if exit_hit else None
    summary = trunc(errors[0].message, 140) if errors else f"Step failed with exit code {code}"
    return [obs("unknown", "weak", NAME, summary, hits, anchor=hits[0].index, errors=errors[:10], signals=[exit_hit[0].text.strip()] if exit_hit else [],
                hint="No known tool signature matched. Read the flagged lines before the exit code; the failing command is the nearest 'Run ...' group above them.")]
