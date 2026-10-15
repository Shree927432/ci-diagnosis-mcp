"""Generate the synthetic fixture corpus (SPEC AC-1/AC-2). Run once; outputs are committed and hashed.

Each case: <name>/job.log, job.json, expected.json. Logs use GitHub's timestamp prefix and step groups.
"""
from __future__ import annotations

import hashlib
import json
import zlib
import pathlib
import shutil

ROOT = pathlib.Path(__file__).parent
T0 = "2026-10-15T10:00:"


def ts(sec: int) -> str:
    return f"{T0}{sec:02d}.0000000Z "


def job_log(step: str, body: list[str], exit_code: int | None = 1, pre: list[str] | None = None) -> str:
    rows = [ts(0) + "##[group]Run actions/checkout@v4", ts(1) + "Syncing repository: acme/app", ts(2) + "##[endgroup]",
            ts(5) + "##[group]Run install", ts(6) + "$ install deps"] + [ts(7) + l for l in (pre or ["added 400 packages"])] + [ts(29) + "##[endgroup]",
            ts(30) + f"##[group]Run {step}", ts(30) + "$ " + step, ts(30) + "##[endgroup]"]
    rows += [ts(31 + min(i // 4, 8)) + l for i, l in enumerate(body)]
    if exit_code is not None:
        rows.append(ts(40) + f"##[error]Process completed with exit code {exit_code}.")
    rows += [ts(41) + "Post job cleanup.", ts(42) + "Cleaning up orphan processes"]
    return "\n".join(rows) + "\n"


def job_json(step: str, times=(30, 40), prev=(5, 29), name="test", conclusion="failure") -> dict:
    s = lambda n, num, c, a, b: {"name": n, "number": num, "status": "completed", "conclusion": c, "started_at": f"{T0}{a:02d}Z", "completed_at": f"{T0}{b:02d}Z"}
    return {"id": 1000 + zlib.crc32(step.encode()) % 9000, "name": name, "conclusion": conclusion, "run_attempt": 1,
            "steps": [s("Run actions/checkout@v4", 1, "success", 0, 2), s("install", 2, "success", *prev), s(step, 3, conclusion if conclusion != "success" else "failure", *times), s("Post job cleanup", 4, "success", 41, 42)]}


CASES: dict[str, dict] = {}


def case(name, mode, parser, step, body, *, exit_code=1, errors=(), tests=(), files=(), window="failed_step", times=(30, 40), prev=(5, 29), pre=None, job_name="test", conclusion="failure", origin=None):
    CASES[name] = dict(mode=mode, parser=parser, step=step, body=body, exit_code=exit_code, errors=list(errors), tests=list(tests), files=list(files), window=window, times=times, prev=prev, pre=pre, job_name=job_name, conclusion=conclusion, origin=origin)


JEST = ["", "> app@1.0.0 test", "> jest", "", "PASS src/utils.test.ts", "FAIL src/cart.test.ts", "  ● Cart › applies discount", "", "    expect(received).toBe(expected) // Object.is equality", "", "    Expected: 90", "    Received: 100", "", "      14 |     const cart = new Cart([{ price: 100 }]);", "      15 |     cart.applyDiscount(10);", "    > 16 |     expect(cart.total()).toBe(90);", "         |                          ^", "", "      at Object.<anonymous> (src/cart.test.ts:16:26)", "", "Test Suites: 1 failed, 1 passed, 2 total", "Tests:       1 failed, 3 passed, 4 total", "Snapshots:   0 total", "Time:        1.8 s", "Ran all test suites."]
case("jest-assert", "test_assertion", "js_tests", "npm test", JEST, errors=["Expected: 90"], tests=["Cart › applies discount"], files=["src/cart.test.ts"])
case("jest-assert-wrong-ts", "test_assertion", "js_tests", "npm test", JEST, tests=["Cart › applies discount"], window="widened", times=(45, 50))
case("jest-suite-failed", "test_load", "js_tests", "npm test", ["FAIL src/api.test.ts", "  ● Test suite failed to run", "", "    Cannot find module './client' from 'src/api.test.ts'", "", "      1 | import { client } from './client';", "", "Test Suites: 1 failed, 1 total", "Tests:       0 total"], errors=["Test suite failed to run"], files=["src/api.test.ts"])
case("vitest-assert", "test_assertion", "js_tests", "npx vitest run", [" RUN  v2.1.4 /home/runner/work/app/app", "", " ❯ src/sum.test.ts (2 tests | 1 failed) 12ms", "   × adds numbers", "     → expected 3 to be 4 // Object.is equality", "", " FAIL  src/sum.test.ts > adds numbers", "AssertionError: expected 3 to be 4 // Object.is equality", "", "- Expected", "+ Received", "", "- 4", "+ 3", "", " ❯ src/sum.test.ts:5:19", "", " Test Files  1 failed (1)", "      Tests  1 failed | 1 passed (2)", "   Start at  10:00:31", "   Duration  420ms"], tests=["adds numbers"], files=["src/sum.test.ts"])
case("mocha-fail", "test_assertion", "js_tests", "npm test", ["", "  Parser", "    ✓ parses numbers", "    1) parses strings", "", "", "  1 passing (20ms)", "  1 failing", "", "  1) Parser", "       parses strings:", "", "      AssertionError: expected 'a' to equal 'b'", "      + expected - actual", "", "      -a", "      +b", "      ", "      at Context.<anonymous> (test/parser.test.js:12:30)", ""], tests=["parses strings"], files=["test/parser.test.js"])

PYTEST = ["============================= test session starts ==============================", "platform linux -- Python 3.12.3, pytest-8.3.2", "collected 12 items", "", "tests/test_models.py ........                                             [ 66%]", "tests/test_billing.py .F..                                               [100%]", "", "=================================== FAILURES ===================================", "____________________________ test_invoice_total ________________________________", "", "    def test_invoice_total():", "        inv = Invoice(items=[Item(price=100)])", ">       assert inv.total() == 90", "E       assert 100 == 90", "E        +  where 100 = <bound method Invoice.total of Invoice(...)>()", "", "tests/test_billing.py:14: AssertionError", "=========================== short test summary info ============================", "FAILED tests/test_billing.py::test_invoice_total - assert 100 == 90", "========================= 1 failed, 11 passed in 0.42s ========================="]
case("pytest-assert", "test_assertion", "pytest", "pytest -q", PYTEST, errors=["assert 100 == 90"], tests=["tests/test_billing.py::test_invoice_total"], files=["tests/test_billing.py"])
case("pytest-assert-wrong-ts", "test_assertion", "pytest", "pytest -q", PYTEST, tests=["tests/test_billing.py::test_invoice_total"], window="whole_log", times=(100, 110), prev=(90, 99))
case("pytest-collection-error", "test_load", "pytest", "pytest", ["============================= test session starts ==============================", "collected 3 items / 1 error", "", "==================================== ERRORS ====================================", "_________________ ERROR collecting tests/test_api.py ___________________________", "ImportError while importing test module '/home/runner/work/app/app/tests/test_api.py'.", "Hint: make sure your test modules/packages have valid Python names.", "Traceback:", "  File \"/usr/lib/python3.12/importlib/__init__.py\", line 90, in import_module", "    return _bootstrap._gcd_import(name[level:], package, level)", "tests/test_api.py:3: in <module>", "    from app.client import Client", "E   ModuleNotFoundError: No module named 'app.client'", "=========================== short test summary info ============================", "ERROR tests/test_api.py", "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!", "=============================== 1 error in 0.31s ==============================="], exit_code=2, errors=["ModuleNotFoundError"], files=["tests/test_api.py"])
case("unittest-fail", "test_assertion", "pytest", "python -m unittest", ["test_add (tests.test_calc.CalcTests.test_add) ... ok", "test_div (tests.test_calc.CalcTests.test_div) ... FAIL", "", "======================================================================", "FAIL: test_div (tests.test_calc.CalcTests.test_div)", "----------------------------------------------------------------------", "Traceback (most recent call last):", "  File \"/home/runner/work/app/app/tests/test_calc.py\", line 11, in test_div", "    self.assertEqual(div(1, 0), None)", "AssertionError: inf != None", "", "----------------------------------------------------------------------", "Ran 2 tests in 0.001s", "", "FAILED (failures=1)"], tests=["tests.test_calc.CalcTests.test_div"], files=["tests/test_calc.py"])

GO = ["=== RUN   TestParse", "=== RUN   TestParse/empty", "    parser_test.go:21: Parse(\"\") = nil, want error", "--- FAIL: TestParse (0.00s)", "    --- FAIL: TestParse/empty (0.00s)", "=== RUN   TestRender", "--- PASS: TestRender (0.00s)", "FAIL", "FAIL\tgithub.com/acme/app/internal/parser\t0.014s", "ok  \tgithub.com/acme/app/internal/render\t0.010s"]
case("gotest-fail", "test_assertion", "gotest", "go test ./...", GO, tests=["TestParse/empty"], files=["parser_test.go"])
case("go-build-error", "compile", "gotest", "go build ./...", ["# github.com/acme/app/internal/parser", "internal/parser/parser.go:42:9: undefined: tokenize", "internal/parser/parser.go:57:2: declared and not used: buf"], errors=["undefined: tokenize"], files=["internal/parser/parser.go"])
# Added in T9 after the real corpus: its directory was written alone, since write() deletes the real cases.
case("rustc-compile-error", "compile", "cargo", "cargo build --locked", ["   Compiling app v0.1.0 (/home/runner/work/app/app)", "error[E0308]: mismatched types", "  --> src/billing.rs:14:5", "   |", "12 | fn total(items: &[Item]) -> u64 {", "   |                             --- expected `u64` because of return type", "13 |     let sum: i64 = items.iter().map(|i| i.price).sum();", "14 |     sum", "   |     ^^^ expected `u64`, found `i64`", "", "error[E0425]: cannot find value `rate` in this scope", " --> src/tax.rs:8:14", "  |", "8 |     amount * rate", "  |              ^^^^ not found in this scope", "", "Some errors have detailed explanations: E0308, E0425.", "For more information about an error, try `rustc --explain E0308`.", "error: could not compile `app` (lib) due to 2 previous errors"],
     exit_code=101, errors=["mismatched types"], files=["src/billing.rs", "src/tax.rs"], job_name="build")
# Added in T10, written alone like rustc-compile-error.
case("clippy-deny-warnings", "lint_format", "cargo", "cargo clippy --all-targets -- -D warnings", ["    Checking app v0.1.0 (/home/runner/work/app/app)", "error: this `if` has identical blocks", "  --> src/billing.rs:22:20", "   |", "22 |       if discount {", "   |  ____________________^", "23 | |         total", "24 | |     } else {", "   | |_____^", "   |", "   = help: for further information visit https://rust-lang.github.io/rust-clippy/master/index.html#if_same_then_else", "   = note: `-D clippy::if-same-then-else` implied by `-D warnings`", "   = help: to override `-D warnings` add `#[allow(clippy::if_same_then_else)]`", "", "error: unused variable: `rate`", " --> src/tax.rs:4:9", "  |", "4 |     let rate = 0.2;", "  |         ^^^^ help: if this is intentional, prefix it with an underscore: `_rate`", "  |", "  = note: `-D unused-variables` implied by `-D warnings`", "  = help: to override `-D warnings` add `#[allow(unused_variables)]`", "", "error: could not compile `app` (lib) due to 2 previous errors"],
     exit_code=101, errors=["identical blocks"], files=["src/billing.rs", "src/tax.rs"], job_name="lint")
case("nextest-timeout", "timeout", "cargo", "cargo nextest run --profile ci", ["    Starting 3 tests across 2 binaries", "        PASS [   0.012s] (1/3) app billing::tests::totals", "        PASS [   0.020s] (2/3) app tax::tests::rates", "     TIMEOUT [  60.004s] (3/3) app::sync sync_finishes_before_shutdown", "  stdout ───", "", "    running 1 test", "", "────────────", "     Summary [  60.031s] 3 tests run: 2 passed, 1 timed out, 0 skipped", "     TIMEOUT [  60.004s] (3/3) app::sync sync_finishes_before_shutdown", "error: test run failed"],
     exit_code=100, errors=["timed out"], tests=["sync_finishes_before_shutdown"], files=[])

TSC = ["", "> app@1.0.0 typecheck", "> tsc --noEmit", "", "src/cart.ts(27,5): error TS2322: Type 'string' is not assignable to type 'number'.", "src/cart.ts(31,18): error TS2339: Property 'totl' does not exist on type 'Cart'.", "src/api/client.ts(8,1): error TS1005: ';' expected.", "", "Found 3 errors in 2 files.", "", "Errors  Files", "     2  src/cart.ts:27", "     1  src/api/client.ts:8"]
case("tsc-errors", "compile", "tsc", "npm run typecheck", TSC, exit_code=2, errors=["TS2322"], files=["src/cart.ts", "src/api/client.ts"])
case("tsc-errors-wrong-ts", "compile", "tsc", "npm run typecheck", TSC, exit_code=2, window="widened", times=(45, 50))
case("mypy-errors", "compile", "py_lint", "mypy app", ["app/billing.py:14: error: Incompatible return value type (got \"str\", expected \"int\")  [return-value]", "app/billing.py:22: error: Argument 1 to \"charge\" has incompatible type \"None\"; expected \"Card\"  [arg-type]", "Found 2 errors in 1 file (checked 14 source files)"], errors=["Incompatible return value type"], files=["app/billing.py"])
case("python-syntaxerror", "compile", "traceback", "python -m app", ["Traceback (most recent call last):", "  File \"/home/runner/work/app/app/app/__main__.py\", line 2, in <module>", "    from app.server import run", "  File \"/home/runner/work/app/app/app/server.py\", line 18", "    def run(:", "            ^", "SyntaxError: invalid syntax"], errors=["SyntaxError"], files=["app/server.py"])

case("eslint-errors", "lint_format", "eslint", "npm run lint", ["", "> app@1.0.0 lint", "> eslint src", "", "", "/home/runner/work/app/app/src/cart.ts", "  12:7   error  'total' is assigned a value but never used  @typescript-eslint/no-unused-vars", "  40:3   error  Unexpected console statement            no-console", "", "/home/runner/work/app/app/src/api/client.ts", "  3:1   warning  Missing return type on function  @typescript-eslint/explicit-module-boundary-types", "", "✖ 3 problems (2 errors, 1 warning)", ""], errors=["no-unused-vars"], files=["src/cart.ts"])
case("ruff-errors", "lint_format", "py_lint", "ruff check .", ["app/billing.py:3:8: F401 [*] `os` imported but unused", "app/billing.py:19:5: E722 Do not use bare `except`", "tests/test_billing.py:1:1: I001 [*] Import block is un-sorted or un-formatted", "Found 3 errors.", "[*] 2 fixable with the `--fix` option."], errors=["F401"], files=["app/billing.py"])
case("prettier-check", "lint_format", "format", "npx prettier --check .", ["Checking formatting...", "[warn] src/cart.ts", "[warn] src/api/client.ts", "[warn] Code style issues found in 2 files. Run Prettier with --write to fix."], files=["src/cart.ts", "src/api/client.ts"])
case("black-check", "lint_format", "format", "black --check .", ["would reformat /home/runner/work/app/app/app/billing.py", "would reformat /home/runner/work/app/app/tests/test_billing.py", "", "Oh no! 💥 💔 💥", "2 files would be reformatted, 11 files would be left unchanged."], files=["app/billing.py"])

case("npm-eresolve", "dependency", "node", "npm ci", ["npm error code ERESOLVE", "npm error ERESOLVE could not resolve", "npm error", "npm error While resolving: app@1.0.0", "npm error Found: react@18.3.1", "npm error node_modules/react", "npm error   react@\"^18.0.0\" from the root project", "npm error", "npm error Could not resolve dependency:", "npm error peer react@\"^17.0.0\" from react-widgets@4.6.1", "npm error node_modules/react-widgets", "npm error   react-widgets@\"^4.6.1\" from the root project", "npm error", "npm error Fix the upstream dependency conflict, or retry", "npm error this command with --force or --legacy-peer-deps", "npm error A complete log of this run can be found in: /home/runner/.npm/_logs/2026-10-15T10_00_31_000Z-debug-0.log"], errors=["ERESOLVE"], files=["package.json"])
case("pip-no-matching", "dependency", "pip", "pip install -r requirements.txt", ["Collecting fastapi==0.999.0 (from -r requirements.txt (line 3))", "ERROR: Could not find a version that satisfies the requirement fastapi==0.999.0 (from versions: 0.100.0, 0.110.0, 0.115.0)", "ERROR: No matching distribution found for fastapi==0.999.0"], errors=["No matching distribution"], files=["requirements.txt"])
case("node-cannot-find-module", "dependency", "node", "node dist/server.js", ["node:internal/modules/cjs/loader:1228", "  throw err;", "  ^", "", "Error: Cannot find module 'pino'", "Require stack:", "- /home/runner/work/app/app/dist/logger.js", "- /home/runner/work/app/app/dist/server.js", "    at Module._resolveFilename (node:internal/modules/cjs/loader:1225:15)", "    at Object.<anonymous> (/home/runner/work/app/app/dist/logger.js:3:14) {", "  code: 'MODULE_NOT_FOUND',", "  requireStack: [ '/home/runner/work/app/app/dist/logger.js' ]", "}", "", "Node.js v20.17.0"], errors=["Cannot find module 'pino'"], files=["dist/logger.js"])
case("pnpm-outdated-lockfile", "dependency", "node", "pnpm install --frozen-lockfile", ["Lockfile is up to date, resolution step is skipped", " ERR_PNPM_OUTDATED_LOCKFILE  Cannot install with \"frozen-lockfile\" because pnpm-lock.yaml is not up to date with package.json", "", "Note that in CI environments this setting is true by default. If you still need to run install in such cases, use \"pnpm install --no-frozen-lockfile\"", "", "    Failure reason:", "    specifiers in the lockfile ({\"zod\":\"^3.22.0\"}) don't match specs in package.json ({\"zod\":\"^3.23.0\"})"], errors=["ERR_PNPM_OUTDATED_LOCKFILE"], files=["pnpm-lock.yaml"])
case("uv-no-solution", "dependency", "pip", "uv sync --locked", ["  × No solution found when resolving dependencies:", "  ╰─▶ Because app depends on pydantic>=2.9 and sqlmodel==0.0.14 depends on pydantic<2.0, we can conclude that your requirements are unsatisfiable.", "", "  hint: Pre-releases are available for `pydantic` in the requested range"], errors=["No solution found"], files=["pyproject.toml"])

case("python-traceback", "runtime", "traceback", "python scripts/migrate.py", ["Applying migration 0007_orders", "Traceback (most recent call last):", "  File \"/home/runner/work/app/app/scripts/migrate.py\", line 41, in <module>", "    main()", "  File \"/home/runner/work/app/app/scripts/migrate.py\", line 33, in main", "    apply(settings[\"DATABASE_URL\"])", "KeyError: 'DATABASE_URL'"], errors=["KeyError: 'DATABASE_URL'"], files=["scripts/migrate.py"])
case("node-typeerror", "runtime", "node", "node scripts/build.js", ["/home/runner/work/app/app/scripts/build.js:27", "  const out = config.output.path.replace(/\\\\/g, '/');", "                            ^", "", "TypeError: Cannot read properties of undefined (reading 'path')", "    at build (/home/runner/work/app/app/scripts/build.js:27:29)", "    at Object.<anonymous> (/home/runner/work/app/app/scripts/build.js:60:1)", "    at Module._compile (node:internal/modules/cjs/loader:1364:14)", "", "Node.js v20.17.0"], errors=["TypeError: Cannot read properties of undefined"], files=["scripts/build.js"])

case("gha-missing-input", "config", "gha", "actions/upload-artifact@v4", ["##[error]Input required and not supplied: path"], errors=["Input required and not supplied: path"])
case("command-not-found", "config", "gha", "make lint", ["make: golangci-lint: No such file or directory", "/bin/sh: 1: golangci-lint: not found", "make: *** [Makefile:12: lint] Error 127"], exit_code=2, errors=["golangci-lint: not found"])
case("resource-not-accessible", "config", "gha", "gh pr comment", ["##[error]Resource not accessible by integration", "##[error]HttpError: Resource not accessible by integration", "    at /home/runner/work/_actions/actions/github-script/v7/dist/index.js:9537:21"], errors=["Resource not accessible by integration"])

case("node-heap-oom", "out_of_memory", "gha", "npm run build", ["", "> app@1.0.0 build", "> next build", "", "   Creating an optimized production build ...", "", "<--- Last few GCs --->", "", "[1234:0x5c4a000]    61234 ms: Mark-Compact 2041.3 (2087.5) -> 2039.9 (2088.0) MB, 1234.5 / 0.0 ms  (average mu = 0.102, current mu = 0.041) allocation failure; scavenge might not succeed", "", "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory", " 1: 0xb8d1a0 node::Abort() [node]", " 2: 0xb0c2e9  [node]"], exit_code=134, errors=["JavaScript heap out of memory"])
case("exit-137", "out_of_memory", "gha", "pytest -n 8", ["============================= test session starts ==============================", "collected 2400 items", "", "tests/test_heavy.py ........................                              [ 10%]", "Killed"], exit_code=137, errors=["exit code 137"])

case("job-timeout", "timeout", "gha", "npm run e2e", ["Running 120 tests using 4 workers", "  ✓  1 login.spec.ts:5 › logs in (2.1s)", "  -  2 checkout.spec.ts:9 › pays with card", "##[error]The job running on runner GitHub Actions 12 has exceeded the maximum execution time of 60 minutes.", "##[error]The operation was canceled."], exit_code=None, errors=["exceeded the maximum execution time"], conclusion="timed_out")
case("step-timeout", "timeout", "gha", "npm run e2e", ["Running 120 tests using 4 workers", "  -  2 checkout.spec.ts:9 › pays with card", "##[error]The action 'npm run e2e' has timed out after 15 minutes."], exit_code=None, errors=["has timed out after 15 minutes"], conclusion="cancelled")

case("fail-fast-cancel", "cancelled", "gha", "pytest", ["tests/test_models.py ....                                                [ 40%]", "##[error]The job was canceled because \"ubuntu-latest_3_11\" failed.", "##[error]The operation was canceled."], exit_code=None, errors=["was canceled because"], conclusion="cancelled", job_name="test (ubuntu-latest, 3.12)")
case("operation-canceled", "cancelled", "gha", "npm test", ["PASS src/a.test.ts", "##[error]The operation was canceled."], exit_code=None, errors=["The operation was canceled"], conclusion="cancelled")

case("runner-lost", "infra", "gha", "npm test", ["PASS src/a.test.ts", "##[error]The hosted runner: GitHub Actions 7 lost communication with the server. Anything in your workflow that terminates the runner process, starves it for CPU/Memory, or blocks its network access can cause this error."], exit_code=None, errors=["lost communication with the server"])
case("docker-rate-limit", "infra", "gha", "docker pull postgres:16", ["Error response from daemon: toomanyrequests: You have reached your pull rate limit. You may increase the limit by authenticating and upgrading: https://www.docker.com/increase-rate-limit"], errors=["toomanyrequests"])
case("no-space-left", "infra", "gha", "npm run build", ["   Creating an optimized production build ...", "Error: ENOSPC: no space left on device, write", "    at Object.writeSync (node:fs:923:3)"], errors=["no space left on device"])

# Adversarial: no recognisable tool signature. Expected mode unknown (a correct unknown counts as accurate).
case("custom-script-exit-1", "unknown", "generic", "./scripts/deploy.sh staging", ["Uploading bundle to staging...", "Bundle size: 4.2 MB", "Deploy step 3 of 5", "Deploy did not finish, see dashboard"], errors=[])
case("silent-exit", "unknown", "generic", "./scripts/check.sh", ["checking…", "done"], exit_code=3, errors=["exit code 3"])
case("bazel-wrapped", "unknown", "generic", "bazel build //...", ["Loading: ", "Loading: 0 packages loaded", "Analyzing: 12 targets (3 packages loaded, 0 targets configured)", "INFO: Analyzed 12 targets (48 packages loaded, 612 targets configured).", "INFO: Found 12 targets...", "[1 / 40] [Prepa] BazelWorkspaceStatusAction stable-status.txt", "ERROR: /home/runner/work/app/app/svc/BUILD:14:11: Compiling svc/main.cc failed: (Exit 1): gcc failed: error executing CppCompile command (from target //svc:main)", "INFO: Elapsed time: 12.345s, Critical Path: 3.21s", "INFO: 7 processes: 7 internal.", "ERROR: Build did NOT complete successfully"], errors=["Build did NOT complete"])


def write() -> str:
    for d in ROOT.iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    for name, c in CASES.items():
        d = ROOT / name
        d.mkdir()
        (d / "job.log").write_text(job_log(c["step"], c["body"], c["exit_code"], c["pre"]))
        (d / "job.json").write_text(json.dumps(job_json(c["step"], c["times"], c["prev"], c["job_name"], c["conclusion"]), indent=1))
        expected = {"mode": c["mode"], "parser": c["parser"], "slice": {"window": c["window"], "mismatch": False},
                    "errors_contain": c["errors"], "tests_contain": c["tests"], "files_contain": c["files"]}
        (d / "expected.json").write_text(json.dumps(expected, indent=1))
    return tree_hash()


def tree_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(ROOT.rglob("*")):
        if p.is_file() and p.name != "TREE_SHA256" and p.suffix != ".pyc":
            h.update(str(p.relative_to(ROOT)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    digest = write()
    (ROOT / "TREE_SHA256").write_text(digest + "\n")
    print(len(CASES), "cases;", digest)
