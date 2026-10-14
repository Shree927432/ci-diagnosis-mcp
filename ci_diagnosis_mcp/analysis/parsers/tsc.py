"""TypeScript compiler diagnostics."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, dedupe_errors, find_all, find_last, obs, plural, trunc

NAME = "tsc"
_ERR = re.compile(r"^(?:##\[error\])?\s*((?:\.{0,2}/)?[\w./@-]+\.(?:[cm]?[jt]sx?|vue|svelte))(?:\((\d+),(\d+)\)|:(\d+):(\d+))\s*[-:]?\s*error\s+(TS\d{4,5}):\s*(.*)$")
_FOUND = re.compile(r"^Found (\d+) errors?")


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    hits = find_all(lines, _ERR)
    if not hits:
        return []
    errors = [ExtractedError(trunc(m.group(7)), l.index + 1, m.group(1), int(m.group(2) or m.group(4)), int(m.group(3) or m.group(5)), m.group(6)) for l, m in hits]
    found = find_last(lines, _FOUND)
    errors = dedupe_errors(errors)
    n = int(found[1].group(1)) if found else len(errors)
    codes = ", ".join(dict.fromkeys(e.code for e in errors if e.code))[:60]
    return [obs("compile", "decisive", NAME, f"TypeScript: {plural(n, 'type error')} in {len({e.file for e in errors})} file(s) ({codes})", [l for l, _ in hits] + ([found[0]] if found else []),
                errors=errors, signals=[found[0].text.strip()] if found else [], hint="Fix the errors in source order; later errors are often cascades of the first.")]
