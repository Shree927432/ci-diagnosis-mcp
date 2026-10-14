"""Spot checks of parser extraction beyond the corpus: counts, locations, repetition."""
from ci_diagnosis_mcp.analysis.normalize import parse_lines
from ci_diagnosis_mcp.analysis.parsers import gha, js_tests, pytest as pyt, tsc
from ci_diagnosis_mcp.analysis.parsers._util import ParseContext


def test_tsc_extracts_file_line_col_code_and_dedupes():
    log = "src/a.ts(3,5): error TS2322: bad\nsrc/a.ts(3,5): error TS2322: bad\nsrc/b.ts:9:1 - error TS1005: ';' expected.\nFound 2 errors."
    o = tsc.parse(parse_lines(log), ParseContext())[0]
    assert o.mode == "compile" and o.weight == "decisive" and len(o.errors) == 2
    assert (o.errors[0].file, o.errors[0].line, o.errors[0].column, o.errors[0].code) == ("src/a.ts", 3, 5, "TS2322")
    assert o.claimed == (0, 3) and "2 type errors" in o.summary


def test_network_error_weight_depends_on_step_kind():
    log = "request to https://registry.npmjs.org/x failed, reason: ECONNRESET"
    install = gha.parse(parse_lines(log), ParseContext("npm ci"))[0]
    tests = gha.parse(parse_lines(log), ParseContext("npm test"))[0]
    assert (install.mode, install.weight) == ("infra", "strong") and (tests.mode, tests.weight) == ("test_assertion", "medium")


def test_timeout_beats_generic_cancel_in_same_log():
    log = "##[error]The job running on runner X has exceeded the maximum execution time of 30 minutes.\n##[error]The operation was canceled."
    modes = {o.mode: o.weight for o in gha.parse(parse_lines(log), ParseContext())}
    assert modes == {"timeout": "decisive", "cancelled": "medium"}


def test_jest_reports_each_failed_test_with_file():
    log = "FAIL src/a.test.ts\n  ● suite › one\n\n    expect(1).toBe(2)\n\n      at Object.<anonymous> (src/a.test.ts:4:3)\n\n  ● suite › two\n\n    boom\n\nTests:       2 failed, 0 passed, 2 total"
    o = js_tests.parse(parse_lines(log), ParseContext())[0]
    assert [t.name for t in o.failed_tests] == ["suite › one", "suite › two"] and o.failed_tests[0].file == "src/a.test.ts"
    assert o.errors[0].line == 4 and o.anchor == 1


def test_pytest_merges_short_summary_and_failure_section():
    log = "=== FAILURES ===\n___ test_x ___\n    def test_x():\n>       assert 1 == 2\nE       assert 1 == 2\n\ntests/test_m.py:7: AssertionError\n=== short test summary info ===\nFAILED tests/test_m.py::test_x - assert 1 == 2\n=== 1 failed in 0.1s ==="
    o = pyt.parse(parse_lines(log), ParseContext())[0]
    assert o.mode == "test_assertion" and len(o.failed_tests) == 1 and o.failed_tests[0].message == "assert 1 == 2"
    assert any(e.file == "tests/test_m.py" and e.line == 7 for e in o.errors)
