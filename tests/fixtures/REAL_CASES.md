# Real-log cases (T8)

Raw job logs from public GitHub Actions runs, fetched 2026-10-15 with `gh api .../actions/jobs/<id>/logs`, untouched. Labels were assigned by reading each log's failing lines. Synthetic cases remain only for modes with no real example (timeout, out_of_memory, most dependency), the three wrong-timestamp cases, the three adversarial unknown cases, go-build-error for the gotest parser floor, and rustc-compile-error (T9) for the cargo compile path.

| case | mode | parser | repo | job | url |
|---|---|---|---|---|---|
| real-action-input-invalid | config | gha | psf/requests | action | https://github.com/psf/requests/actions/runs/32539111078/job/96945403587 |
| real-cancelled-by-github | cancelled | gha | astral-sh/ruff | benchmarks instrumented ty (simulation: micro-m-z) | https://github.com/astral-sh/ruff/actions/runs/38632017529/job/116088507754 |
| real-cancelled-make-test | cancelled | gha | pydantic/pydantic | test | https://github.com/pydantic/pydantic/actions/runs/37679497078/job/112991530746 |
| real-cancelled-windows-build | cancelled | gha | vitest-dev/vitest | Browsers: rest 2/2, node-24, windows-latest | https://github.com/vitest-dev/vitest/actions/runs/38533675491/job/115751422836 |
| real-download-artifact-none | config | gha | pydantic/pydantic | upload-previews | https://github.com/pydantic/pydantic/actions/runs/38592295505/job/115950014518 |
| real-esbuild-resolve-entry | dependency | node | typescript-eslint/typescript-eslint | Build All Packages | https://github.com/typescript-eslint/typescript-eslint/actions/runs/38526415008/job/115727836722 |
| real-eslint-problem-matcher | lint_format | eslint | jestjs/jest | TypeScript Compatibility | https://github.com/jestjs/jest/actions/runs/38417607927/job/115367869272 |
| real-eslint-unused-vars | lint_format | eslint | prettier/prettier | Lint | https://github.com/prettier/prettier/actions/runs/38388990867/job/115267761895 |
| real-jest-leak-detection | test_load | js_tests | jestjs/jest | Node LTS on Ubuntu with leak detection | https://github.com/jestjs/jest/actions/runs/38560274458/job/115839668227 |
| real-jest-worker-sigsegv | test_load | js_tests | jestjs/jest | macOS with shard 3/3 / Node v24.x | https://github.com/jestjs/jest/actions/runs/38417607927/job/115366658576 |
| real-mocha-eslint | test_assertion | js_tests | eslint/eslint | Test (ubuntu-latest, 24.x) | https://github.com/eslint/eslint/actions/runs/37487000067/job/112349520274 |
| real-mypy-tox | compile | py_lint | pallets/flask | typing | https://github.com/pallets/flask/actions/runs/37633292268/job/112832844965 |
| real-nextest-fail | test_assertion | cargo | astral-sh/ruff | cargo test (namespace-profile-macos-15) | https://github.com/astral-sh/ruff/actions/runs/38695941708/job/116292515080 |
| real-node-enoent-unhandled | runtime | node | vitest-dev/vitest | Merge Reports | https://github.com/vitest-dev/vitest/actions/runs/38574765085/job/115888844770 |
| real-precommit-ruff | lint_format | format | pallets/flask | main | https://github.com/pallets/flask/actions/runs/34727211038/job/103643345311 |
| real-precommit-ruff-format | lint_format | format | psf/requests | lint | https://github.com/psf/requests/actions/runs/34145369235/job/101816141171 |
| real-prettier-check | lint_format | format | eslint/eslint | Verify Files | https://github.com/eslint/eslint/actions/runs/38171304253/job/114564306189 |
| real-puppeteer-download-ebusy | infra | gha | prettier/prettier | Browser(firefox) on Windows | https://github.com/prettier/prettier/actions/runs/38535590133/job/115758056730 |
| real-pytest-conftest-import | test_load | pytest | pallets/flask | Development Versions | https://github.com/pallets/flask/actions/runs/34252544596/job/102150232254 |
| real-pytest-internalerror | runtime | traceback | pytest-dev/pytest | build (windows-py310-xdist) | https://github.com/pytest-dev/pytest/actions/runs/38121360864/job/114416976161 |
| real-pytest-pypy-attributeerror | test_assertion | pytest | pydantic/pydantic | Test macos-latest / pypy3.11 | https://github.com/pydantic/pydantic/actions/runs/38593926991/job/115955643318 |
| real-pytest-unraisable | test_assertion | pytest | encode/httpx | Python 3.11 | https://github.com/encode/httpx/actions/runs/37009481166/job/110845502341 |
| real-pytest-windows-tox | test_assertion | pytest | pytest-dev/pytest | build (windows-py313) | https://github.com/pytest-dev/pytest/actions/runs/38037725187/job/114171663746 |
| real-pytest-xdist-pexpect | test_assertion | pytest | pytest-dev/pytest | build (ubuntu-py310-xdist) | https://github.com/pytest-dev/pytest/actions/runs/38121360274/job/114417078206 |
| real-unknown-alls-green | unknown | generic | pytest-dev/pytest | check | https://github.com/pytest-dev/pytest/actions/runs/37465844384/job/112283860185 |
| real-unknown-changelog-check | unknown | generic | jestjs/jest | Check Changelog Entry | https://github.com/jestjs/jest/actions/runs/38560274361/job/115839342248 |
| real-unknown-required-checks | unknown | generic | typescript-eslint/typescript-eslint | Required Checks | https://github.com/typescript-eslint/typescript-eslint/actions/runs/38414363554/job/115356290024 |
| real-unknown-twine-check | unknown | generic | encode/httpx | Python 3.12 | https://github.com/encode/httpx/actions/runs/36918360067/job/110557896773 |
| real-upload-artifact-enotfound | infra | gha | pydantic/pydantic | core / Build PGO-optimized on linux / 3.15 | https://github.com/pydantic/pydantic/actions/runs/38610907908/job/116013681622 |
| real-vitest-browser-runner | test_assertion | js_tests | vitest-dev/vitest | Browsers: runner, node-24, windows-latest | https://github.com/vitest-dev/vitest/actions/runs/38603254876/job/115987677124 |
| real-vitest-browser-watch | test_assertion | js_tests | vitest-dev/vitest | Browsers: rest 1/2, node-24, windows-latest | https://github.com/vitest-dev/vitest/actions/runs/38574764432/job/115888912764 |
| real-vitest-snapshot-project | test_assertion | js_tests | vitest-dev/vitest | Test: e2e, node-24, macos-latest | https://github.com/vitest-dev/vitest/actions/runs/38596427719/job/115964350570 |
| real-yarn-ssl-ecanceled | infra | gha | prettier/prettier | Node.js 26 on Windows | https://github.com/prettier/prettier/actions/runs/38578325236/job/115901438251 |
