"""go test, go build, go vet, panics."""
from __future__ import annotations

import re

from ..types import ExtractedError, FailedTest, LogLine
from ._util import ParseContext, dedupe_errors, dedupe_tests, find_all, find_first, obs, plural, pos_of, trunc

NAME = "gotest"
GATE = re.compile(r"""^\s*--- FAIL|^FAIL\s|^panic:|^(?:ok|\?)\s+\S+\s+[\d.]+s|^go: |^# \S+|^=== RUN|\.go:\d+:""", re.M)
_FAIL_TEST = re.compile(r"^\s*--- FAIL: (\S+) \(([\d.]+)s\)")
_FAIL_PKG = re.compile(r"^FAIL\s+(\S+)\s+(?:[\d.]+s|\[build failed\]|\[setup failed\])")
_LOC = re.compile(r"^\s*((?:[\w./-]+/)?[\w.-]+\.go):(\d+):(?:(\d+):)?\s*(.*)$")
_PANIC = re.compile(r"^panic:\s*(.*)$")
_COMPILE_MSG = re.compile(r"^(undefined|cannot use|missing|too many|not enough|imported and not used|declared and not used|syntax error|cannot|invalid|unknown)")
_MARK = re.compile(r"^(?:ok|FAIL|\?)\s+\S+\s+[\d.]+s|^go: |^# \S+|^=== RUN")


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    fails, pkgs, panic = find_all(lines, _FAIL_TEST), find_all(lines, _FAIL_PKG), find_first(lines, _PANIC)
    if not (fails or pkgs or panic or find_first(lines, _MARK)):
        return []
    out = []
    compile_errs, chits = [], []
    for l in lines:
        m = _LOC.match(l.text)
        if m and m.group(4) and _COMPILE_MSG.match(m.group(4)) and not m.group(1).endswith("_test.go"):
            compile_errs.append(ExtractedError(trunc(m.group(4)), l.index + 1, m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None))
            chits.append(l)
    build_failed = any("[build failed]" in l.text for l, _ in pkgs)
    if compile_errs or build_failed:
        out.append(obs("compile", "decisive" if compile_errs else "strong", NAME, f"Go build failed with {plural(len(compile_errs), 'compile error')}", chits or [l for l, _ in pkgs],
                       errors=dedupe_errors(compile_errs), signals=[l.text.strip() for l, _ in pkgs][:5], hint="The package does not compile; fix undefined symbols, unused imports/variables, and type mismatches first."))
    tests, errors, hits = [], [], []
    for l, m in fails:
        hits.append(l)
        pos, loc = pos_of(lines, l.index), None
        for x in lines[max(0, pos - 15): pos + 15]:
            lm = _LOC.match(x.text)
            if lm and lm.group(1).endswith("_test.go"):
                loc = lm
                if x.index > l.index:
                    break
        tests.append(FailedTest(m.group(1), l.index + 1, loc.group(1) if loc else None, trunc(loc.group(4)) if loc else None))
        errors.append(ExtractedError(trunc(loc.group(4) if loc else f"Test {m.group(1)} failed"), l.index + 1, loc.group(1) if loc else None, int(loc.group(2)) if loc else None, test=m.group(1)))
    if tests:
        hits += [l for l, _ in pkgs]
        leaf = [t for t in tests if not any(o.name.startswith(t.name + "/") for o in tests if o is not t)]
        out.append(obs("test_assertion", "decisive", NAME, f"{plural(len(leaf), 'Go test')} failed", hits, anchor=hits[0].index, errors=dedupe_errors(errors), tests=dedupe_tests(tests),
                       signals=[l.text.strip() for l, _ in pkgs][:5], hint="The file_test.go:line message above each --- FAIL explains what differed."))
    if panic and not tests:
        l, m = panic
        pos = pos_of(lines, l.index)
        loc = next((re.match(r"^\s+((?:[\w./-]+/)?[\w.-]+\.go):(\d+)", x.text) for x in lines[pos: pos + 40] if re.match(r"^\s+((?:[\w./-]+/)?[\w.-]+\.go):(\d+)", x.text) and "/go/src/" not in x.text and "runtime/" not in x.text), None)
        out.append(obs("runtime", "strong", NAME, f"Go panic: {trunc(m.group(1), 120)}", [l], errors=[ExtractedError(trunc(m.group(1)), l.index + 1, loc.group(1) if loc else None, int(loc.group(2)) if loc else None)],
                       signals=[l.text.strip()], hint="A goroutine panicked; the first non-runtime frame in the stack is the culprit."))
    return out
