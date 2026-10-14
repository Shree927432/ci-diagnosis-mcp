"""pytest and unittest."""
from __future__ import annotations

import re

from ..types import ExtractedError, FailedTest, LogLine
from ._util import ParseContext, dedupe_errors, dedupe_tests, find_all, find_last, following, obs, plural, pos_of, trunc

NAME = "pytest"
GATE = re.compile(r"""^(?:FAILED|ERROR)\s+\S+\.py|^=+\s|^_{3,}\s|ERROR collecting|during collection|^(?:FAIL|ERROR):\s+\w+\s+\(|^FAILED \(""", re.M)
_SHORT = re.compile(r"^(FAILED|ERROR)\s+(\S+?\.py)(?:::(\S+))?(?:\s+-\s+(.*))?$")
_SUMMARY = re.compile(r"^=+\s.*\b(\d+) (?:failed|error)s?\b.*=+$")
_HEADER = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}$")
_E = re.compile(r"^E\s{3}(.*)$")
_LOC = re.compile(r"^(\S+?\.py):(\d+):\s*(\w*(?:Error|Exception|Failed)\w*)?")
_COLLECT = re.compile(r"ERROR collecting (\S+)|error during collection|errors? during collection")
_UT_FAIL = re.compile(r"^(FAIL|ERROR):\s+(\w+)\s+\(([\w.]+)\)")
_UT_SUMMARY = re.compile(r"^FAILED \((?:failures=(\d+))?(?:,\s*)?(?:errors=(\d+))?\)")
_FILE_LINE = re.compile(r'File "([^"]+)", line (\d+)')


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    shorts, headers = find_all(lines, _SHORT), find_all(lines, _HEADER)
    summary, collect = find_last(lines, _SUMMARY), find_all(lines, _COLLECT)
    ut_fails, ut_summary = find_all(lines, _UT_FAIL), find_last(lines, _UT_SUMMARY)
    if not shorts and not headers and not ut_fails and not collect:
        return []
    tests, errors, hits = [], [], []
    for l, m in shorts:
        hits.append(l)
        file, name = m.group(2), f"{m.group(2)}::{m.group(3)}" if m.group(3) else m.group(2)
        tests.append(FailedTest(name, l.index + 1, file, trunc(m.group(4)) if m.group(4) else None))
        errors.append(ExtractedError(trunc(m.group(4) or f"{m.group(1)} {name}"), l.index + 1, file, test=name))
    for l, m in headers:
        name = m.group(1).strip()
        if re.match(r"^(?:FAILURES|ERRORS|warnings summary|short test summary info)$", name, re.I):
            continue
        hits.append(l)
        pos, loc, elines = pos_of(lines, l.index), None, []
        for x in lines[pos + 1:]:
            if _HEADER.match(x.text) or re.match(r"^=+\s", x.text) or re.match(r"^-{3,}\s+Captured", x.text):
                break
            e = _E.match(x.text)
            if e:
                elines.append(e.group(1))
            pl = _LOC.match(x.text)
            if pl and "site-packages" not in pl.group(1):
                loc = pl
        msg = trunc(" | ".join(t for t in elines if t.strip())[:300])
        existing = next((t for t in tests if t.name.endswith("::" + name) or name in t.name), None)
        if existing:
            existing.message = existing.message or (msg or None)
        else:
            tests.append(FailedTest(name, l.index + 1, loc.group(1) if loc else None, msg or None))
        errors.append(ExtractedError(msg or f"{(loc.group(3) if loc else None) or 'Failure'} in {name}", l.index + 1, loc.group(1) if loc else None, int(loc.group(2)) if loc else None, loc.group(3) if loc else None, test=name))
    for l, m in ut_fails:
        hits.append(l)
        name = f"{m.group(3)}.{m.group(2)}"
        block = following(lines, pos_of(lines, l.index), 15, _UT_FAIL)
        msg = next((t for t in reversed(block) if re.match(r"^\w*(?:Error|Exception|Failed)\b", t)), "")
        fl = next((_FILE_LINE.search(t) for t in reversed(block) if _FILE_LINE.search(t)), None)
        file = fl.group(1) if fl else None
        tests.append(FailedTest(name, l.index + 1, file, trunc(msg) or None))
        errors.append(ExtractedError(trunc(msg or f"{m.group(1)}: {name}"), l.index + 1, file, int(fl.group(2)) if fl else None, test=name))
    for s in (summary, ut_summary):
        if s:
            hits.append(s[0])
    tests = dedupe_tests(tests)
    n = len(tests)
    if summary:
        n = sum(int(x) for x in re.findall(r"(\d+) (?:failed|error)", summary[0].text)) or n
    elif ut_summary:
        n = int(ut_summary[1].group(1) or 0) + int(ut_summary[1].group(2) or 0) or n
    is_load = bool(collect) or any(re.search(r"ImportError|ModuleNotFoundError|error collecting|SyntaxError", e.message) for e in errors)
    tool = "unittest" if ut_fails and not shorts else "pytest"
    weight = "decisive" if (summary or ut_summary or shorts) else "strong"
    anchor = next((h.index for h in hits if _HEADER.match(h.text) and not re.match(r"^_{3,}\s+(?:FAILURES|ERRORS)", h.text)), hits[0].index)
    if is_load:
        for l, _ in collect:
            hits.append(l)
        return [obs("test_load", weight, NAME, f"{tool}: {plural(n, 'test module')} failed to import or collect", hits, anchor=anchor, errors=dedupe_errors(errors), tests=tests,
                    signals=[summary[0].text.strip()] if summary else [], hint="A test module failed to import. Fix the ImportError/ModuleNotFoundError first; assertion failures may be downstream.")]
    return [obs("test_assertion", weight, NAME, f"{plural(n, tool + ' test')} failed", hits, anchor=anchor, errors=dedupe_errors(errors), tests=tests,
                signals=[s[0].text.strip() for s in (summary, ut_summary) if s], hint='Assertions failed. The "E" lines show the failing assertion; the file:line above them is the exact location.')]
