"""Python tracebacks outside a test runner."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, find_all, obs, pos_of, trunc

NAME = "traceback"
GATE = re.compile(r"""^Traceback \(most recent call last\):""", re.M)
_START = re.compile(r"^Traceback \(most recent call last\):")
_FRAME = re.compile(r'^\s+File "([^"]+)", line (\d+), in (.+)$')
_EXC = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)):\s*(.*)$")
_LIB = re.compile(r"site-packages|dist-packages|/lib/python|<frozen|importlib")


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    starts = find_all(lines, _START)
    if not starts:
        return []
    start = starts[-1][0]  # the last traceback is the one that killed the process
    pos = pos_of(lines, start.index)
    frame = user = exc = None
    for x in lines[pos + 1: pos + 200]:
        f = _FRAME.match(x.text)
        if f:
            frame = f
            if not _LIB.search(f.group(1)):
                user = f
            continue
        e = _EXC.match(x.text)
        if e:
            exc = (e, x)
            break
    if not exc:
        return []
    (e, el), fr = exc, (user or frame)
    name, msg = e.group(1), e.group(2)
    is_import, is_syntax = bool(re.search(r"ModuleNotFoundError|ImportError", name)), bool(re.search(r"SyntaxError|IndentationError", name))
    err = ExtractedError(trunc(f"{name}: {msg}"), el.index + 1, fr.group(1) if fr else None, int(fr.group(2)) if fr else None, code=name)
    # SyntaxError reports its own location on the line after the frame list.
    if is_syntax:
        for x in lines[pos + 1: pos + 200]:
            sm = re.match(r'^\s+File "([^"]+)", line (\d+)$', x.text)
            if sm:
                err.file, err.line = sm.group(1), int(sm.group(2))
    mode = "dependency" if is_import else "compile" if is_syntax else "runtime"
    weight = "decisive" if is_syntax else "strong"
    hint = ("A module could not be imported: the dependency is missing from the manifest or not installed in CI, or the import path is wrong." if is_import
            else "The file does not parse; fix the syntax at the reported location." if is_syntax
            else f"Unhandled exception{' in ' + fr.group(3) if fr else ''}; the deepest non-library frame is the place to start.")
    return [obs(mode, weight, NAME, f"Python {name}: {trunc(msg, 120)}", [start, el], anchor=start.index, errors=[err], signals=[start.text.strip(), trunc(f'{name}: {msg}', 150)], hint=hint)]
