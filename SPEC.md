# Product specification — ci-diagnosis-mcp

> Status: v2, approved for v0.1 after independent review (DECISION-6af94eab); non-goals and risks revised after v0.2 shipped (T8 real-log corpus, T9/T10 cargo parser). A coding agent must not implement product code until this specification is approved through Genesis.
> Source design: DESIGN.md v2. This specification is the binding subset for release v0.1; DESIGN.md holds rationale and later phases.

## Problem

When CI fails, a coding agent must find the failed run, download large noisy logs, locate the failing step, work out what failed and why, and decide what to fix. Today it does this by hand with `gh` and raw logs, spending many tool calls and tokens, and it often fixes the wrong thing, for example editing source to make a flaky or pre-existing failure pass.

The desired outcome is an MCP server that returns, in one or two calls, a structured diagnosis: what failed (mode), why it probably failed (origin, as a probability distribution with reasons), the exact errors, tests and files involved, a focused denoised log excerpt, the suggested fix, and the next most informative evidence to gather. The server does not call a model; the agent reasons over the result.

## Users

- Coding agents connected over MCP stdio: Claude Code, Cursor, Codex and any MCP client. They call the tools and act on the structured result.
- Developers running those agents, who review the agent's fix and read the markdown rendering.
- Maintainers of this server, who add parsers for new tools. Python readers; each parser is one file.

## Functional requirements

- FR-1: `start_investigation` accepts `repo` and exactly one of `run_id`, `job_id`, `pr_number`, `branch`, `sha`, plus optional `run_attempt` (default: latest). For `pr_number`, `branch` and `sha` it selects completed runs for the head SHA whose conclusion is `failure`, `timed_out`, or `startup_failure` (`cancelled` only when no run failed); with exactly one it investigates, with several it returns `candidate_runs[]` and investigates none, with none it says so and lists the latest runs with their status. A run whose status is not `completed` returns `is_error` naming the run as in progress.
- FR-2: Failed jobs are those with conclusion `failure` or `timed_out`, or `cancelled` when no job failed. The server downloads their logs for the chosen attempt, at most `max_jobs` (default 8) prioritised by distinct job-name prefix, at most 4 concurrently, streaming each into the disk cache, and reports progress through the MCP context after each job. Logs over 20 MB are truncated to their last 20000 lines and the response says so. A run with zero jobs and conclusion `startup_failure` is reported as mode `config` with the workflow path and the run's error message, without downloads.
- FR-3: Logs are normalised: lines truncated to 2000 characters before any matching, ANSI escapes and timestamps stripped, `##[...]` and `::...::` workflow commands parsed, progress noise dropped, and rendered with original line numbers; error lines are flagged. Excerpts are placed in a fence whose backtick run is longer than any run inside the excerpt and labelled as untrusted log data.
- FR-4: Each log is sliced to the failed step by timestamp window; the slice is verified to contain an error or exit-code line and widened to the previous step if not, then to the whole log. The whole log, pre-filtered to candidate lines, is parsed every time; if its mode beats the slice mode by weight class the whole-log mode is reported. The response exposes `slice: {window: failed_step | widened | whole_log, mismatch: bool}`.
- FR-5: Parsers exist for jest/vitest/mocha, pytest/unittest, go test and go build, tsc, eslint/biome, ruff/mypy/pyright/pylint, prettier/black/ruff-format, npm/pnpm/yarn, pip/poetry/uv, Python tracebacks, Node exceptions, GitHub Actions config and infrastructure messages, plus a generic fallback. Each returns observations with a weight class, claimed line ranges, `errors[]` with repo-relative file, line, column, code, `failed_tests[]`, and an anchor line. Runner prefixes (`/home/runner/work/<r>/<r>/`, `D:\a\<r>\<r>\`, `/__w/<r>/<r>/`) are stripped from paths. Identical repeated lines count once.
- FR-6: Mode is the argmax over parser observations with line ownership (specific parser beats generic inside claimed ranges) times a `step_context` prior from the failed step's name, with a confidence band: decisive alone → high, strong alone → medium, generic only → low with mode `unknown`. `mode_runner_up` and `mode_margin` are reported.
- FR-7: Failed jobs are clustered by signature (mode plus hash of the first error message); the response carries one `log_excerpt` per cluster, a representative job, and the ids of other jobs sharing it.
- FR-8: Origin is a posterior over `this_change`, `pre_existing`, `flaky`, `environment`, `external` per DESIGN.md §8 with the global prior (this_change 0.55, flaky 0.18, environment 0.12, external 0.10, pre_existing 0.05): per channel the max of supports times the min of againsts, product across observed channels, tempering `T` = 0.7. A channel is observed when it produced at least one observation; `channels_observed` lists them.
- FR-9: The cost-0 channels `matrix_pattern` and `attempts` are gathered inside `start_investigation`. Attempts observations are relative to the attempt under investigation: another attempt of the same run passed → flaky strong, external medium, this_change strong against; another attempt failed with the same level-1 signature (job name plus failed step name) → flaky strong against, this_change medium, pre_existing medium; another attempt failed with a different level-1 signature → flaky medium.
- FR-10: `gather_evidence` performs one of `fetch_sibling_jobs`, `fetch_attempts`, `fetch_annotations`, `fetch_pr_history`, `fetch_pr_diff`, `fetch_base_history`, `fetch_full_step_log`, adds the resulting observations to the named channel, and returns `evidence_added`, origin before and after, and the fetched payload. Cross-run comparisons use level-1 signatures from the jobs API only; no extra logs are downloaded. Errors are actionable: expired log (410) says to rerun or use another job; insufficient scope names the missing permission.
- FR-11: `next_actions` ranks untaken, available actions by `voi / cost` per DESIGN.md §10. An action is available when its preconditions hold (`fetch_pr_diff` and `fetch_pr_history` need a pull request; `fetch_sibling_jobs` needs more than one job; `fetch_attempts` needs attempt > 1 or a later attempt); unavailable actions are listed in `unavailable_actions[]` with the reason. Overrides: when the top origin is `this_change` and `fetch_pr_diff` is available and unobserved it is ranked first; when mode is `unknown`, `fetch_full_step_log` is first. The top three are returned with `recommendation` of `gather` or `stop` and `stop_reason`. Precedence: (1) the action-equivalence shortcut stops when mode confidence is not low and the top two origins share a suggested action, overriding the channel requirement; (2) otherwise `stop` requires mode confidence not low, two observed origin channels with one outside {matrix_pattern, attempts}, and either high origin confidence or best remaining score below 0.05; (3) when no action is available and mode confidence is not low, `stop` with `stop_reason = no_actions_available`.
- FR-13: `suggested_actions[]` come from the fix matrix keyed by (mode, top origin) per DESIGN.md §5.
- FR-14: `get_investigation` returns the full state including `evidence_trail` and linked attempts. Investigation ids are `owner/repo#run_id@attempt`; an unknown id returns `is_error` telling the agent to call `start_investigation` for that run.
- FR-15: `record_outcome` stores `actual_mode`, `actual_origin`, `fix_summary`, optional `fix_commit`, `fix_files[]`, `resolved_by` (`code_fix`, `test_fix`, `rerun`, `config_fix`, `dependency_fix`, `ignored`), a snapshot of evidence and `channels_observed`, and `derived_origin` from these ordered rules, first match wins: (1) `resolved_by` is `rerun` and `fix_files` is empty → `flaky`; (2) every fix file matches a test glob (`tests/**`, `test/**`, `spec/**`, `**/__tests__/**`, `**/test_*.py`, `**/*_test.*`, `**/*.test.*`, `**/*.spec.*`) → `this_change`; (3) any fix file is under `.github/workflows/` → `this_change` if the investigation's `diff_overlap` channel observed a workflow change, else `environment`; (4) `fix_files` is non-empty → `this_change`; (5) otherwise `null`. Outcomes do not change priors in v0.1.
- FR-16: `analyze_log` is stateless: it runs normalisation, parsers and mode only on supplied `log_text` (at most 20 MB, else `is_error`), returns errors, tests, files, excerpt, and the fix-matrix suggestion for `assumed_origin: this_change`.
- FR-17: Every tool returns markdown `content` for reading and `structured_content` validated against a published output schema. Required keys for `start_investigation`: `investigation_id`, `run` (`id`, `url`, `workflow_path`, `head_sha`, `head_branch`, `attempt`, `event`, `pull_requests`), `clusters[]` (`signature`, `representative_job` with `job_id`, `name`, `url`, `failed_step`, `failing_command`, `exit_code`, `errors`, `failed_tests`, `files`, `log_excerpt`, `slice`; `other_jobs`), `jobs_not_downloaded`, `mode`, `mode_confidence`, `mode_runner_up`, `mode_margin`, `origin`, `origin_confidence`, `margin`, `channels_observed`, `suggested_actions`, `next_actions`, `unavailable_actions`, `recommendation`, `stop_reason`, `evidence_added`, `calibration`. Required keys for `analyze_log`: `mode`, `mode_confidence`, `errors`, `failed_tests`, `files`, `log_excerpt`, `slice`, `suggested_actions`, `assumed_origin`. Errors return `is_error` with an actionable message (missing token, not found, rate limited, in progress, expired log, missing scope).
- FR-18: A prompt `fix_ci_failure` describes the gather-until-stop, fix, record-outcome loop.
- FR-19: Authentication resolves `GITHUB_TOKEN`, then `GH_TOKEN`, then `gh auth token`, in that order; `GITHUB_API_URL` overrides the API base for GitHub Enterprise; a missing token yields an actionable error rather than unauthenticated calls. The `Authorization` header is sent only to the API host; log-download redirects to other hosts are fetched without it. Required token scopes are documented: `actions`, `contents`, `pull_requests`, `checks`, all read.
- FR-20: Output budgets: 6000 characters per cluster excerpt, 20000 total, `errors[]` ≤ 30, `failed_tests[]` ≤ 50, `files[]` ≤ 25 per cluster, with remainder counts; `evidence_trail` only from `get_investigation`.

## Non-functional requirements

- NFR-1: `analyze_log` on a 10 MB log completes in under 2 s on a laptop; parsers see only pre-filtered candidate lines plus a window around them.
- NFR-2: `start_investigation` with 4 failed jobs of 10 MB each completes in under 20 s at p95 on replayed API traffic.
- NFR-3: Peak resident memory stays under 500 MB for 8 jobs of 20 MB; logs are processed one at a time.
- NFR-4: At most 40 GitHub API calls per `start_investigation` including cost-0 actions; blob-storage redirects for log downloads count as calls.
- NFR-5: Nothing but MCP protocol frames is written to stdout; diagnostics go to stderr.
- NFR-6: At startup the server sets umask 077, creates `~/.ci-diagnosis-mcp` (override `CI_DIAGNOSIS_STATE`) with mode 0700, and all files inside, including SQLite `-wal` and `-shm`, inherit 0600. `repo` is validated against `^[\w.-]+/[\w.-]+$` and cache files are named by numeric job id. Cached logs older than 7 days are deleted at startup; SQLite runs in WAL mode with a 5 s busy timeout.
- NFR-7: Direct runtime dependencies are only `mcp` (>=2,<3) and `httpx`; Python 3.10 or later.
- NFR-8: Results are deterministic for identical inputs and identical stored state; no randomness and no model calls.
- NFR-9: On the fixture corpus, mode accuracy is at least 90 percent and the unknown rate at most 15 percent, where unknown rate counts cases predicted `unknown` whose expected mode is not `unknown`; a correct `unknown` counts as accurate.
- NFR-10: Adding a parser requires one new module and one fixture directory; no change to the belief or server layers.

## Constraints

- Python 3.10+, official `mcp` SDK 2.x (`MCPServer`), `httpx`; no other direct runtime dependency. Dev-only: pytest, pytest-asyncio, respx.
- Transport is stdio only in v0.1.
- GitHub Actions only in v0.1; the analysis layer must not import the GitHub client.
- The server never triggers reruns or any write to GitHub.
- Trust boundary: log content is untrusted data. It is parsed with regular expressions after per-line truncation, never executed or passed to a shell, and relayed to the agent only inside a labelled fence that it cannot close. Tokens are never logged, written to cache, or returned in any tool output.
- Build budget: a two to three hour build by one agent session; scope is the requirements above, nothing else.
- Process: Genesis ledger; Ponytail full; fixtures and expected results are committed before parser code; the only human gates are specification and plan approval; model reviews are recorded as decisions.

## Non-goals

- `rerun_job` or any GitHub write.
- `add_evidence` and `retract_evidence` tools (cut from v0.1 on review; the agent can reason without them).
- Level-2 signature comparison across runs (extra log downloads); per-repo prior updates from outcomes; rebuilding an investigation from GitHub when state is missing.
- `status_page`, `duration`, and `compare_with_last_green` channels and actions.
- Parsers for gradle/maven/junit and docker build; the generic fallback covers them. (cargo test/nextest/rustc/clippy shipped in v0.2; rustfmt and gofmt are matched by the format parser but have no real fixture yet.)
- The replay evaluation of the next-action policy (DESIGN.md §20.2 evidence efficiency); v0.1 unit-tests the ranking formula only.
- Passive outcome inference, learned likelihoods, isotonic calibration, and the `stats` telemetry command; v0.1 only records the data they need.
- Streamable HTTP transport, GitLab or other CI providers.
- The agent end-to-end benchmark (DESIGN.md §20.4).

## Acceptance criteria

- AC-1: `python -m pytest` passes, and a coverage test asserts at least two fixture cases per mode listed in DESIGN.md §3 that v0.1 parses, at least one per parser module, and at least three adversarial cases whose expected mode is `unknown`.
- AC-2: A corpus test computes mode accuracy ≥ 0.90 and unknown rate ≤ 0.15 over `tests/fixtures` as defined in NFR-9, and the ledger holds a trace recording that the fixtures were committed before any parser module existed.
- AC-3: Three fixtures with deliberately wrong step timestamps are diagnosed with the expected mode, and each asserts the exact `slice.window` (`widened` or `whole_log`) and `slice.mismatch` values from its `expected.json`.
- AC-5: A budget test with 12 synthetic failed jobs produces at most 8 downloads, at most 20000 excerpt characters, and one excerpt per signature cluster.
- AC-6: An in-process MCP client test lists exactly the tools `start_investigation`, `gather_evidence`, `get_investigation`, `record_outcome`, `analyze_log` and the prompt `fix_ci_failure`, and asserts that the output schemas of `start_investigation` and `analyze_log` require every key listed in FR-17.
- AC-7: A timing test runs `analyze_log` on a generated 10 MB log in which at least 5 percent of lines are candidate lines, in under 2 s.
- AC-8: `record_outcome` derives `flaky` for `resolved_by: rerun` with no files; `this_change` for files only under `tests/`; `environment` for a `.github/workflows` file with no observed workflow change; `this_change` for a mix of a test file and a source file; `null` for `resolved_by: ignored` with no files.
- AC-10: Manual smoke test, recorded as evidence by the builder with the command and output: `start_investigation` against one real public failed run returns a mode other than `unknown`, a non-empty excerpt, and at least one `next_action`, within 20 s.
- AC-11: A mocked-GitHub test counts at most 40 API calls, redirects included, for `start_investigation` on a run with 8 failed jobs and attempt 2.
- AC-12: For a mocked investigation with a known token, a test asserts the token is absent from every tool's `content`, `structured_content`, `is_error` messages, captured stderr, and every file under the cache directory, and that the `Authorization` header was never sent to the blob host.
- AC-13: A log containing a line of 100000 characters and a line of ten backticks followed by injected instructions is analysed within the time limit, the excerpt fence is longer than ten backticks, the excerpt is labelled untrusted, and the excerpt text is still present verbatim inside the fence.
- AC-14: After startup the state directory has mode 0700, the database and its `-wal` file have mode 0600, a `repo` of `../x` or `a/b/c` is rejected with `is_error`, and a cache file name contains only the numeric job id.
- AC-15: A subprocess test runs the server over stdio, performs one `analyze_log` call, and asserts stdout contains only JSON-RPC frames; a determinism test runs `analyze_log` twice on the same input and asserts byte-identical structured output.
- AC-16: With two failed runs mocked for one PR, `start_investigation` returns `candidate_runs[]` with both and no `investigation_id`; with one, it investigates. A token-resolution test asserts the order `GITHUB_TOKEN`, `GH_TOKEN`, `gh auth token` and that `GITHUB_API_URL` changes the request host.

## Risks

- Wrong step slice corrupts every downstream field. Mitigation: FR-4 always parses the whole log too and exposes `slice`; AC-3.
- Parser coverage on the long tail (Nx, Turbo, Bazel wrappers; Gradle, Docker deferred). Mitigation: strong generic fallback; NFR-9 measured on the corpus; one-file parser contract (NFR-10).
- Synthetic fixtures may flatter the parsers because the same session writes both. Mitigation: fixtures committed first with a ledger trace (AC-2), adversarial unknown cases (AC-1), AC-10 live smoke; the real-log corpus of 33 public jobs added in T8.
- Hostile log content relayed to the agent. Mitigation: FR-3 fencing and labelling, AC-13.
- Hand weights are uncalibrated. Mitigation: `calibration: uncalibrated` in every response; outcomes recorded from day one (FR-15).
- Agents ignore the belief and read only the excerpt. Mitigation: none in v0.1; measured later by DESIGN.md §20.4.
- Build-time risk. Mitigation: review cuts applied; the generic fallback ships first so every other parser is additive; order of build is fixtures, core, parsers, belief, client, investigation, server.

## Open questions

- None blocking v0.1. DESIGN.md §18 lists deferred items (band thresholds after corpus, status page scope, `analyze_log` accepting `job.json`).
