"""jest, vitest and mocha."""
from __future__ import annotations

import re

from ..types import ExtractedError, FailedTest, LogLine
from ._util import ParseContext, dedupe_tests, find_all, find_first, find_last, following, obs, plural, pos_of, trunc

NAME = "js_tests"
GATE = re.compile(r"""^Tests:\s|^Test Suites:|^\s*FAIL\s|^\s*Tests\s+\d+ failed|\bvitest\b|RUN\s+v\d|\d+ failing""", re.M)
_FAIL_FILE = re.compile(r"^\s*FAIL\s+(\S+\.(?:[cm]?[jt]sx?))(?:\s|$)")
_BULLET = re.compile(r"^\s*●\s+(.+)$")
_JEST_SUMMARY = re.compile(r"^Tests:\s+(\d+) failed")
_JEST_SUITES = re.compile(r"^Test Suites:\s+(\d+) failed")
_JEST_STOP = re.compile(r"^\s*●\s+|^(?:Test Suites:|Tests:|Snapshots:|Time:|Ran all)")
_V_FAIL = re.compile(r"^\s*FAIL\s+(?:\S+\s+)??(\S+\.(?:[cm]?[jt]sx?))(?::\d+)?\s*>\s*(.+)$")  # optional project or browser label, optional :line
_V_X = re.compile(r"^\s*(?:×|✗|✘)\s+(.+?)(?:\s+\d+ms)?$")
_V_SUMMARY = re.compile(r"^\s*Tests\s+(\d+) failed")
_V_MARK = re.compile(r"\bvitest\b|RUN\s+v\d+\.\d+")
_STACK = re.compile(r"(?:❯|at)\s+(?:.*?\()?((?:\.{0,2}/)?[\w./@-]+\.(?:[cm]?[jt]sx?)):(\d+)(?::(\d+))?\)?")
_MOCHA_FAILING = re.compile(r"^\s*(\d+) failing\s*$")
_MOCHA_ITEM = re.compile(r"^\s*(\d+)\)\s+(.+)$")
_NM = re.compile(r"node_modules")


def _loc(block: list[str]):
    for t in block:
        m = _STACK.search(t)
        if m and not _NM.search(m.group(1)):
            return m
    return None


def _jest(lines):
    summary, suites = find_last(lines, _JEST_SUMMARY), find_last(lines, _JEST_SUITES)
    bullets = find_all(lines, _BULLET)
    if not summary and not suites:
        return []
    tests, errors, hits, load = [], [], [], 0
    current = None
    for l in lines:
        ff = _FAIL_FILE.match(l.text)
        if ff:
            current = ff.group(1)
            hits.append(l)
            continue
        b = _BULLET.match(l.text)
        if not b:
            continue
        name = b.group(1).strip()
        hits.append(l)
        block = following(lines, pos_of(lines, l.index), 14, _JEST_STOP, blanks_ok=True)
        if name.startswith("Test suite failed to run"):
            load += 1
            loc = _loc(block)
            errors.append(ExtractedError(trunc("Test suite failed to run: " + " ".join(block[:3])), l.index + 1, loc.group(1) if loc else current, int(loc.group(2)) if loc else None))
            continue
        msg = trunc(" ".join(t for t in block if not re.match(r"^\s*(?:>|\d+\s*\||at\s)", t))[:300])
        tests.append(FailedTest(name, l.index + 1, current, msg or None))
        loc = _loc(block)
        errors.append(ExtractedError(msg or name, l.index + 1, loc.group(1) if loc else current, int(loc.group(2)) if loc else None, int(loc.group(3)) if loc and loc.group(3) else None, test=name))
    for s in (summary, suites):
        if s:
            hits.append(s[0])
    tests = dedupe_tests(tests)
    n = int(summary[1].group(1)) if summary else len(tests)
    out = []
    if n or tests:
        out.append(obs("test_assertion", "decisive" if summary else "strong", NAME, f"{plural(n, 'Jest test')} failed", hits, anchor=(tests[0].log_line - 1) if tests else hits[0].index,
                       errors=[e for e in errors if e.test], tests=tests, signals=[summary[0].text.strip()] if summary else [],
                       hint="Assertions failed. Compare expected vs received; decide whether the change altered the behaviour under test or the expectation is stale."))
    if load:
        out.append(obs("test_load", "decisive", NAME, f"{plural(load, 'Jest suite')} failed to run", hits, anchor=next(e.log_line - 1 for e in errors if e.test is None),
                       errors=[e for e in errors if e.test is None], signals=[suites[0].text.strip()] if suites else [],
                       hint="Test files could not be loaded (import or compile error); fix the module error before looking at assertions."))
    return out


def _vitest(lines):
    summary = find_last(lines, _V_SUMMARY)
    fails = find_all(lines, _V_FAIL)
    if not summary and not fails:
        return []
    if not find_first(lines, _V_MARK) and not fails:
        return []
    tests, errors, hits = [], [], []
    for l, m in fails:
        hits.append(l)
        block = following(lines, pos_of(lines, l.index), 14, _V_FAIL, blanks_ok=True)
        msg = next((t for t in block if re.search(r"Error|expected|received", t)), block[0] if block else "")
        tests.append(FailedTest(m.group(2).strip(), l.index + 1, m.group(1), trunc(msg) or None))
        loc = _loc(block)
        errors.append(ExtractedError(trunc(msg or m.group(2)), l.index + 1, loc.group(1) if loc else m.group(1), int(loc.group(2)) if loc else None, test=m.group(2).strip()))
    if not tests:
        current = None
        for l in lines:
            f = re.match(r"^\s*(?:❯|FAIL)\s+(\S+\.(?:[cm]?[jt]sx?))", l.text)
            if f:
                current = f.group(1)
            x = _V_X.match(l.text)
            if x:
                tests.append(FailedTest(x.group(1).strip(), l.index + 1, current))
                hits.append(l)
    if summary:
        hits.append(summary[0])
    tests = dedupe_tests(tests)
    n = int(summary[1].group(1)) if summary else len(tests)
    return [obs("test_assertion", "decisive" if summary else "strong", NAME, f"{plural(n, 'Vitest test')} failed", hits, anchor=(tests[0].log_line - 1) if tests else hits[0].index,
                errors=errors, tests=tests, signals=[summary[0].text.strip()] if summary else [], hint="Assertions failed; inspect expected vs received and the source location in the stack.")]


def _mocha(lines):
    failing = find_last(lines, _MOCHA_FAILING)
    if not failing:
        return []
    pos = pos_of(lines, failing[0].index)
    tests, errors, hits = [], [], [failing[0]]
    for l in lines[pos + 1:]:
        m = _MOCHA_ITEM.match(l.text)
        if not m:
            continue
        hits.append(l)
        block = following(lines, pos_of(lines, l.index), 12, _MOCHA_ITEM, blanks_ok=True)
        name = " ".join([m.group(2).strip()] + [t for t in block if not re.search(r"Error|at\s|expected", t)][:1]).rstrip(":")
        msg = next((t for t in block if re.search(r"Error|expected", t)), "")
        tests.append(FailedTest(trunc(name, 200), l.index + 1, None, trunc(msg) or None))
        loc = _loc(block)
        errors.append(ExtractedError(trunc(msg or name), l.index + 1, loc.group(1) if loc else None, int(loc.group(2)) if loc else None, test=name))
    return [obs("test_assertion", "decisive", NAME, f"{plural(int(failing[1].group(1)), 'Mocha test')} failed", hits, anchor=(tests[0].log_line - 1) if tests else failing[0].index,
                errors=errors, tests=dedupe_tests(tests), signals=[failing[0].text.strip()], hint="Assertions failed; see the numbered failures after the summary.")]


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    return _jest(lines) + _vitest(lines) + _mocha(lines)
