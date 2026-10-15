"""cargo test, cargo-nextest, rustc compile errors, Rust panics."""
from __future__ import annotations

import re

from ..types import ExtractedError, FailedTest, LogLine
from ._util import ParseContext, dedupe_errors, find_all, find_first, obs, plural, trunc

NAME = "cargo"
GATE = re.compile(r"""^\s*FAIL \[|test result: FAILED|^\s*test .+ \.\.\. FAILED$|^\s*---- .+ stdout ----$|panicked at|^error\[E\d{4}\]|^\s*--> \S+\.rs:\d+|could not compile""", re.M)
_NEXTEST = re.compile(r"^\s*FAIL \[\s*[\d.]+s\]\s+(?:\(\s*\d+/\d+\)\s+)?(\S+)\s+(\S+)")  # binary-id, test name
_LIBTEST = re.compile(r"^\s*test (.+?) \.\.\. FAILED$|^\s*---- (.+?) stdout ----$")
_PANIC = re.compile(r"^\s*thread '([^']*)'(?: \(\d+\))? panicked at (?:'(.*)', )?(\S+?\.rs):(\d+):(\d+)")  # 'msg', only before Rust 1.73
_SUMMARY = re.compile(r"^\s*(?:test result: FAILED\.|Summary \[.*\b\d+ failed|error: test (?:run )?failed)")
_NX_SUMMARY = re.compile(r"^\s*Summary \[")
_ERROR = re.compile(r"^error(?:\[(E\d{4})\])?: (.+)$")
_LOC = re.compile(r"^\s*--> (\S+?\.rs):(\d+):(\d+)")
_COULD_NOT = re.compile(r"^error: could not compile")


def _next_text(lines: list[LogLine], i: int, span: int = 1) -> list[LogLine]:
    """Lines right after lines[i] in the original log (the pre-filter may leave gaps)."""
    return [n for n in lines[i + 1: i + 1 + span] if n.index - lines[i].index <= span]


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    out = []
    errs, chits = [], []
    for i, l in enumerate(lines):
        m = _ERROR.match(l.text)
        if not m or _COULD_NOT.match(l.text):
            continue
        loc = next((x for x in (_LOC.match(n.text) for n in _next_text(lines, i, 3)) if x), None)
        if loc or m.group(1):  # an uncoded "error:" counts only with a --> *.rs location (ruff and ty print the same layout for .py)
            errs.append(ExtractedError(trunc(m.group(2)), l.index + 1, loc.group(1) if loc else None, int(loc.group(2)) if loc else None, int(loc.group(3)) if loc else None, code=m.group(1)))
            chits.append(l)
    could_not = find_first(lines, _COULD_NOT)
    if errs or could_not:
        hits = chits + ([could_not[0]] if could_not else [])
        out.append(obs("compile", "decisive", NAME, f"Rust build failed with {plural(len(errs), 'compile error')}" if errs else "Rust build failed", hits, anchor=hits[0].index,
                       errors=dedupe_errors(errs), signals=[could_not[0].text.strip()] if could_not else [], hint="The crate does not compile; fix the first error at its --> location, later errors are often downstream."))

    panics = {}
    for i, l in enumerate(lines):
        m = _PANIC.match(l.text)
        if m and m.group(1) not in panics:
            msg = m.group(2) or next((n.text.strip() for n in _next_text(lines, i) if n.text.strip()), "")
            panics[m.group(1)] = (l, m, trunc(msg or "panicked"))
    nx = find_all(lines, _NEXTEST)
    found = {f"{m.group(1)} {m.group(2)}": (l, m.group(2)) for l, m in reversed(nx)}  # reversed: the first occurrence wins
    if not found:
        found = {(m.group(1) or m.group(2)): (l, m.group(1) or m.group(2)) for l, m in reversed(find_all(lines, _LIBTEST))}
    tests, errors = [], []
    for name, (l, thread) in sorted(found.items(), key=lambda kv: kv[1][0].index):
        p = panics.get(thread)
        file, line, col = (p[1].group(3), int(p[1].group(4)), int(p[1].group(5))) if p else (None, None, None)
        tests.append(FailedTest(name, l.index + 1, file, p[2] if p else None))
        errors.append(ExtractedError(p[2] if p else f"Test {name} failed", (p[0] if p else l).index + 1, file, line, col, test=name))
    if tests:
        summ = find_all(lines, _SUMMARY)
        nx_sum = find_first(lines, _NX_SUMMARY)
        test_hits = [l for l, _ in nx] or [l for l, _ in find_all(lines, _LIBTEST)]
        # nextest reprints every failure after its Summary line; that copy sits next to the final verdict
        anchor = next((l for l in test_hits if nx_sum and l.index > nx_sum[0].index), None) or next((l for l in test_hits if "stdout ----" in l.text), test_hits[0])
        hits = test_hits + [l for l, _ in summ] + [p[0] for p in panics.values()]
        out.append(obs("test_assertion", "decisive" if summ else "strong", NAME, f"{plural(len(tests), 'Rust test')} failed", hits, anchor=anchor.index, errors=errors, tests=tests,
                       signals=list(dict.fromkeys(l.text.strip() for l, _ in summ))[:5], hint="A test panicked; the `panicked at file:line` location and the message under it show what differed."))
    elif panics:
        l, m, msg = next(iter(panics.values()))
        out.append(obs("runtime", "strong", NAME, f"Rust panic in thread '{m.group(1)}': {trunc(msg, 120)}", [l], errors=[ExtractedError(msg, l.index + 1, m.group(3), int(m.group(4)), int(m.group(5)))],
                       signals=[l.text.strip()], hint="A thread panicked outside a test; the `panicked at` location is where it happened."))
    return out
