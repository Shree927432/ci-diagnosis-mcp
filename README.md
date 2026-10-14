# ci-diagnosis-mcp

An MCP server that diagnoses failed GitHub Actions runs for coding agents. It downloads the failed jobs' logs, finds the failing step, works out **what** failed (the *mode*) and **why** it probably failed (the *origin*, a probability distribution with the evidence behind it), and returns the errors, failed tests, files, a focused denoised log excerpt, the suggested fix, and the next most informative evidence to gather. It never calls a model: the agent reasons, the server supplies evidence and belief.

## Install

Requires Python 3.10+ and a GitHub token with read access to actions, contents, pull requests and checks.

```bash
pip install ci-diagnosis-mcp      # or: uv tool install ci-diagnosis-mcp
```

From a checkout:

```bash
uv venv && uv pip install -e ".[dev]"
```

The token is resolved from `GITHUB_TOKEN`, then `GH_TOKEN`, then `gh auth token` (the GitHub CLI's login). For GitHub Enterprise set `GITHUB_API_URL=https://ghe.example.com/api/v3`.

## Connect an agent

Claude Code:

```bash
claude mcp add ci-diagnosis -- ci-diagnosis-mcp
```

Cursor, Codex, or any MCP client (`mcp.json` style):

```json
{ "mcpServers": { "ci-diagnosis": { "command": "ci-diagnosis-mcp", "env": { "GITHUB_TOKEN": "..." } } } }
```

Then ask the agent to use the `fix_ci_failure` prompt, or just say "diagnose the CI failure on PR 123".

## Tools

| Tool | What it does |
|---|---|
| `start_investigation(repo, run_id \| job_id \| pr_number \| branch \| sha)` | Resolves the failed run, downloads up to 8 failed job logs (4 concurrently), slices each to its failed step, runs the parsers, clusters jobs by signature, gathers the free evidence channels, and returns the diagnosis. If several failed runs match it lists `candidate_runs` instead. |
| `gather_evidence(investigation_id, action)` | Performs one evidence action and updates the origin belief: `fetch_sibling_jobs`, `fetch_attempts`, `fetch_annotations`, `fetch_pr_history`, `fetch_pr_diff`, `fetch_base_history`, `fetch_full_step_log`. |
| `get_investigation(investigation_id)` | Full state with the evidence trail; lets a new session resume. |
| `record_outcome(investigation_id, actual_mode, actual_origin, fix_summary, …)` | Records what the failure actually was once CI is green; feeds calibration. |
| `analyze_log(log_text)` | Stateless analysis of pasted log text from any CI system. |

Every tool returns markdown for reading and `structured_content` with a published schema. Log excerpts are fenced and labelled as untrusted data; they are never instructions.

## How a diagnosis reads

```
Mode: test_assertion (high confidence)
Origin: this_change 71%, flaky 14%, environment 8%, external 5%, pre_existing 2% (medium; channels: matrix_pattern, diff_overlap)
Suggested fix: Compare the failing test with the diff: source changed and test untouched → fix the code or update the expectation …
Recommendation: stop (origin_confident)
```

Modes: `test_assertion`, `test_load`, `compile`, `lint_format`, `dependency`, `runtime`, `config`, `out_of_memory`, `timeout`, `cancelled`, `infra`, `unknown`.
Origins: `this_change`, `pre_existing`, `flaky`, `environment`, `external`.

The belief update, the next-action ranking and the stop rule are specified in [DESIGN.md](DESIGN.md); the binding requirements for this release are in [SPEC.md](SPEC.md).

## Adding a parser

One file in `ci_diagnosis_mcp/analysis/parsers/` with `NAME`, a `GATE` regex (the parser only runs when it matches the log), and `parse(lines, ctx) -> list[Observation]`. Add a fixture directory under `tests/fixtures/` with `job.log`, `job.json` and `expected.json`, regenerate `TREE_SHA256` with `python tests/fixtures/generate_fixtures.py` if you used the generator, and run `pytest`. Parsers are discovered automatically; nothing else changes.

## State

Investigations, evidence and outcomes live in `~/.ci-diagnosis-mcp/state.db` (override with `CI_DIAGNOSIS_STATE`). Downloaded logs are cached next to it for 7 days. The directory is created with mode 0700 and files with 0600. The token is never written there.

## Development

```bash
.venv/bin/python -m pytest -q          # full suite: parsers, corpus accuracy, belief, client, server, timing
```

The repository is run with the Genesis harness: `.genesis/KICKOFF.md` describes the current state for a cold session and `genesis brief .` gives the active task.

## Status

v0.1. GitHub Actions only, stdio only. Parsers cover jest/vitest/mocha, pytest/unittest (including conftest import errors and INTERNALERROR crashes), go, tsc, eslint/biome (including problem-matcher output), ruff/mypy/pyright/pylint, prettier/black and pre-commit hook output, npm/pnpm/yarn, pip/poetry/uv, Python tracebacks, Node exceptions and GitHub Actions runner messages; everything else falls back to a generic error extractor. The accuracy corpus in `tests/fixtures` holds 32 real failed jobs from public repositories (see `tests/fixtures/REAL_CASES.md`) plus synthetic cases for timeout, out-of-memory and most dependency failures, which no public log in the sample exhibited; mode accuracy on it is 1.00 with an unknown rate of 0.00. Likelihood weights are hand-set and reported as `uncalibrated` until enough outcomes are recorded.
