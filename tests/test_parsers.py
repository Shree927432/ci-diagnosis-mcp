"""Spot checks of parser extraction beyond the corpus: counts, locations, repetition."""
from ci_diagnosis_mcp.analysis.normalize import parse_lines
from ci_diagnosis_mcp.analysis.parsers import cargo, gha, js_tests, pytest as pyt, tsc
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


def test_mypy_pretty_continuation_lines_are_joined():
    from ci_diagnosis_mcp.analysis.parsers import py_lint
    log = 'src/app.py:206: error: Incompatible types in assignment (expression has\n    type "dict[str, Any]", variable has type "str")  [assignment]\nFound 1 error in 1 file (checked 3 source files)'
    o = py_lint.parse(parse_lines(log), ParseContext())[0]
    assert o.errors[0].message.endswith('variable has type "str")') and o.errors[0].code == "assignment"


def test_cargo_test_plain_output_bare_panic_and_python_lookalikes():
    log = ("running 2 tests\ntest tests::adds ... ok\ntest tests::subtracts ... FAILED\n\nfailures:\n\n---- tests::subtracts stdout ----\n\n"
           "thread 'tests::subtracts' panicked at src/lib.rs:20:9:\nassertion `left == right` failed\n  left: 1\n right: 2\n\nfailures:\n    tests::subtracts\n\n"
           "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n\nerror: test failed, to rerun pass `--lib`")
    o = cargo.parse(parse_lines(log), ParseContext("cargo test"))[0]
    assert (o.mode, o.weight, o.anchor) == ("test_assertion", "decisive", 6) and [t.name for t in o.failed_tests] == ["tests::subtracts"]
    assert (o.errors[0].file, o.errors[0].line, o.errors[0].message) == ("src/lib.rs", 20, "assertion `left == right` failed")
    bare = cargo.parse(parse_lines("thread 'main' panicked at src/main.rs:5:9:\nindex out of bounds: the len is 3 but the index is 10"), ParseContext())[0]
    assert (bare.mode, bare.weight, bare.errors[0].file, bare.errors[0].message) == ("runtime", "strong", "src/main.rs", "index out of bounds: the len is 3 but the index is 10")
    # ruff and ty print rustc-style diagnostics for Python files; they are not Rust compile errors
    assert cargo.parse(parse_lines("error[unresolved-import]: Cannot resolve imported module `x`\n --> src/a.py:1:8"), ParseContext()) == []
