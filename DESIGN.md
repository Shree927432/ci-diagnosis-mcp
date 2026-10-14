# CI Diagnosis MCP Server — Design

Status: v2, revised after review. No code written against this yet.

## 1. Goal

Give a coding agent (Claude Code, Cursor, Codex, or any MCP client) a server that:

1. collects evidence about a failed CI run (logs, step metadata, diff, history),
2. maintains a **belief** about the failure that separates *what failed* from *why*,
3. updates that belief as evidence arrives, with an auditable trail,
4. tells the agent which evidence to fetch next and when to stop,
5. returns everything in an agent-native shape: structured fields plus a focused, denoised log excerpt.

The server does not call a model. The agent is the reasoner; the server is the evidence and belief engine.

### Non-goals for v1

- Fixing code. The agent does that.
- A formal POMDP solver. Within one run the root cause does not change and the agent is the planner, so a Bayes update plus a greedy next-action ranking is enough.
- Rerunning jobs. A side effect needing `actions: write`; agents already have `gh run rerun`. Rerun *results* are still used as evidence (section 6).
- Learned likelihoods. V1 ships hand weights and records the data that makes learning possible later (section 14).
- CI providers other than GitHub Actions. The analysis layer is provider-agnostic; GitLab is a later client behind the same tools.

## 2. Core idea: two variables, not one

A single "root cause" label mixes two different questions, and the evidence for each comes from different places.

| Variable | Question | Set by | Uncertainty |
|---|---|---|---|
| **Mode** | What failed? (assertion, compile, lint, dependency, OOM, …) | The log, through parsers | Low: usually settled after one tool call |
| **Origin** | Why? (this change, pre-existing, flaky, environment, outside world) | Diff, history, matrix pattern, reruns | High: this is where belief updating and next-action ranking apply |

The suggested fix is a function of the pair. A test assertion with origin *this change* means fix the code or the expectation; the same mode with origin *flaky* means rerun and file the flake.

Mapping to the POMDP vocabulary:

| POMDP concept | Our implementation |
|---|---|
| Hidden state | (mode, origin) |
| Observations | Evidence items, each assigned to one channel and one variable |
| Belief | Mode: parser verdict with confidence. Origin: posterior over five values |
| Actions | Evidence-gathering actions with numeric cost |
| Policy | Greedy value-of-information per unit cost; the agent decides |
| Reward | Correct (mode, origin) in few calls; measured through recorded outcomes |

## 3. Mode

| id | What the log shows | Parsers |
|---|---|---|
| `test_assertion` | Tests ran and some failed | jest/vitest/mocha, pytest/unittest, go test, cargo test, JVM tests |
| `test_load` | Test files could not be imported or collected | jest "suite failed to run", pytest collection errors |
| `compile` | Type, compile, or syntax error | tsc, mypy/pyright, rustc, javac/kotlinc, go build, Python SyntaxError |
| `lint_format` | Linter or formatter check failed | eslint/biome, ruff/pylint/flake8, clippy, prettier/black/rustfmt/gofmt |
| `dependency` | Install or resolution failed, lockfile out of sync, module not found | npm/pnpm/yarn, pip/poetry/uv, go mod, cargo, docker pull |
| `runtime` | Unhandled exception or panic outside a test runner | Python traceback, Node exception, Go panic |
| `config` | Workflow, action input, permission, secret, missing file or tool | GitHub Actions messages, shell "command not found", setup-* errors |
| `out_of_memory` | OOM killer, heap limit, exit 137 | infra parser |
| `timeout` | Job or step timeout, exit 124 | infra parser |
| `cancelled` | Cancelled by fail-fast sibling, concurrency, or manual cancel | infra parser |
| `infra` | Runner lost, disk full, registry or API rate limit, network during install or checkout | infra parser |
| `unknown` | Nothing matched | generic fallback: `##[error]` lines, exit code, last error-looking lines |

Mode is chosen by the strongest parser match (section 8). It carries its own confidence band, independent of origin.

## 4. Origin

| id | Meaning | Typical fix |
|---|---|---|
| `this_change` | The PR or commit under test introduced the failure | Fix the code, or update the test if the behaviour change was intended; if only tests changed, the new test is wrong |
| `pre_existing` | The base branch fails the same way; this change did not cause it | Do not fix here; point at the base-branch failure |
| `flaky` | Non-deterministic; passes on rerun or fails intermittently with no related change | Rerun now, file or fix the flake |
| `environment` | Runner, workflow, secrets, tool versions, or matrix axis specific | Edit workflow or settings |
| `external` | Something outside the repo changed: unpinned dependency released, registry or service outage | Pin, retry later, or mirror |

Origin is a probability distribution. There is no `unknown` origin; with no origin evidence gathered the belief equals the prior and is reported as such (`channels_observed = []`).

## 5. Fix matrix

Suggested actions come from the (mode, top origin) pair. Representative rows; the full table lives in code.

| Mode | Origin | Suggested action |
|---|---|---|
| test_assertion | this_change | Compare the failing test with the diff: source changed and test untouched → fix code or expectation; only tests changed → fix the test |
| test_assertion | flaky | Rerun; mark or quarantine the test; look for timing, ordering, or network use |
| test_assertion | pre_existing | Already failing on base; do not block on it |
| compile | this_change | Fix the listed file:line errors in order; later errors are often cascades |
| compile | external | An unpinned dependency changed its types; pin it |
| lint_format | this_change | Run the fixer and commit |
| dependency | this_change | Lockfile out of sync with the manifest; regenerate and commit |
| dependency | external | Version disappeared or registry down; pin or retry |
| out_of_memory | this_change | Memory regression; profile the change |
| out_of_memory | environment | Reduce parallelism or raise heap limits for the runner size |
| timeout | this_change | Likely a hang or deadlock introduced by the change; see the last output before the timeout |
| config | environment | Edit the workflow: permissions, secrets, versions, paths |
| cancelled | any | Diagnose the sibling job that failed first |
| infra | external or environment | Rerun; if recurrent, authenticate registries or move to a different runner |

## 6. Evidence channels

Every observation belongs to exactly one channel and feeds exactly one variable. Channels feeding the same variable are treated as independent; observations inside a channel are not.

| Channel | Feeds | Observations | Cost |
|---|---|---|---|
| `log_signature` | mode | Parser matches on the failed step's log, including the exit code line and `##[error]` annotations. Exit code is **not** a separate channel: it appears in the log and parsers already match it | 1 |
| `step_context` | mode prior | Failed step's position and kind (checkout, setup, install, build, lint, test, deploy; `uses:` vs `run:`). Adjusts the mode prior only; never a mode verdict on its own | 0 |
| `matrix_pattern` | origin | All matrix jobs failed with the same signature vs a subset; which axis differs | 0 |
| `attempts` | origin | Earlier attempts of this run and whether they failed with the same signature; links to the previous investigation | 0 |
| `pr_history` | origin | Did earlier pushes of this PR fail the same way? Repeated identical failure across pushes is strong evidence of determinism | 2 |
| `base_history` | origin | Does the base branch fail the same job with the same signature? Failure rate of this test or job over the last N base runs | 3 |
| `diff_overlap` | origin | Which changed files are referenced by errors or failing tests; only tests changed; lockfile changed; workflow changed; dependency manifest changed | 3 |
| `duration` | origin, mode prior | Job duration vs the median of recent successful runs; a 3x outlier suggests a hang | 1 |
| `annotations` | mode | Check-run annotations give file, line, and message without parsing | 1 |
| `rerun_result` | origin | A later attempt of the **same SHA** passed or failed identically. Recorded through `attempts`, never triggered by this server | 0 |
| `status_page` | origin | Active GitHub or registry incident at the time of the run | 1 |
| `agent` | either | Evidence the agent injects after its own reasoning. Must name the channel it replaces so it cannot stack on it | 0 |

**Signature levels.** Comparing failures across runs must not require downloading every log. Two levels are defined:

- *Level 1*: job name plus failed step name. Available from the jobs API for any run, no log needed. Used for all cross-run comparisons in `pr_history`, `base_history`, and `attempts`.
- *Level 2*: mode plus a hash of the first extracted error message. Needs the log. Computed for the run under investigation always, and for at most one other run per history action: the most recent level-1 match on the base branch or the previous push.

A level-1 match alone carries medium weight; a level-2 match carries strong weight.

Observations whose meaning depends on context are defined as conjunctions, not single patterns. "Network error during install or checkout" is `infra`; "network error during a test step" is `test_assertion` with origin leaning `environment` or `this_change`. The flat table cannot express that, so the observation itself carries the step kind.

## 7. Weights

| Weight | Ratio | Used for |
|---|---|---|
| decisive | 50 | Mode signatures that are unambiguous (tsc error codes, pytest summary line, `Runner lost communication`) |
| strong | 8 | Characteristic evidence |
| medium | 3 | Clearly suggestive |
| weak | 1.5 | Mild lean |
| against | 1/3 | Makes the hypothesis less likely |
| strong against | 1/8 | Nearly rules it out |

Representative rows. The full table lives in code and is the thing the calibration loop later replaces.

**Mode (log_signature)**

| Observation | Weight |
|---|---|
| tsc `error TSxxxx` with file:line | compile decisive |
| pytest `=== N failed ===` or jest `Tests: N failed` | test_assertion decisive |
| jest `Test suite failed to run` / pytest `error collecting` | test_load decisive |
| `ERESOLVE`, `No matching distribution`, `OUTDATED_LOCKFILE`, `Cannot find module 'pkg'` | dependency decisive |
| `Cannot find module './relative'` | compile strong (missing file, not a package) |
| exit 137 / `JavaScript heap out of memory` | out_of_memory decisive |
| `exceeded the maximum execution time` / exit 124 | timeout decisive |
| `The job was canceled because "x" failed` | cancelled decisive; everything else strong against |
| `Runner lost communication`, `No space left on device`, `toomanyrequests` | infra decisive |
| network error (`ECONNRESET`, `ENOTFOUND`) **during install or checkout** | infra strong |
| network error **during a test step** | test_assertion medium (the test hit the network) |
| `Resource not accessible by integration`, `Input required and not supplied`, `command not found` | config decisive |
| Python traceback ending in `ModuleNotFoundError` | dependency strong |
| Python traceback ending in any other exception, outside a test runner | runtime strong |

**Origin**

| Observation | Channel | Weights |
|---|---|---|
| all matrix jobs fail with the same signature | matrix_pattern | this_change medium, pre_existing weak; flaky against |
| one of N matrix jobs fails; axis differs | matrix_pattern | environment strong, flaky medium; this_change against |
| same failure on every push of this PR | pr_history | this_change strong, pre_existing medium; flaky strong against |
| failure appeared on the latest push only, earlier pushes green | pr_history | this_change strong |
| base branch fails the same job with the same signature | base_history | pre_existing strong; this_change strong against |
| base branch green; this test failed in ≥ 2 of the last 20 base runs | base_history | flaky strong |
| failing test's file, or source it imports, is in the diff | diff_overlap | this_change strong |
| only test files changed and those tests fail | diff_overlap | this_change strong (the new test is wrong) |
| no file referenced by the failure is in the diff | diff_overlap | this_change against; flaky medium, external medium |
| lockfile or dependency manifest changed, mode is dependency or compile | diff_overlap | this_change strong |
| manifest unchanged and unpinned ranges present, mode is dependency | diff_overlap | external strong |
| workflow file changed, mode is config | diff_overlap | this_change strong |
| job duration > 3x median of recent green runs | duration | this_change medium (hang); mode prior shifts to timeout |
| later attempt of the same SHA passed | rerun_result | flaky strong, external medium; this_change strong against |
| later attempt failed identically | rerun_result | this_change medium, pre_existing medium; flaky strong against |
| active incident on status page | status_page | external strong |

## 8. Update rule

**Mode.** Each parser returns observations with weights and the log line ranges it claims. Where ranges overlap, the more specific parser wins and the generic fallback contributes nothing inside a claimed range. An identical error line repeated `n` times is one observation with a count. Mode score per candidate is the product of its decisive and strong observations times a small prior adjustment from `step_context`. The verdict is the argmax; the mode confidence band comes from the winning weight class and the margin over the runner-up (decisive alone → high; strong alone → medium; only generic fallback → low, mode `unknown`).

**Origin.**

```
prior:        P0(origin)  per-repository count table with Laplace smoothing, global fallback
observation:  o in channel c, with weights w(o, origin); 1 when unspecified

per channel c, per origin h:
    S_c(h) = max over supporting o in c of w(o, h)      (1 if none)
    A_c(h) = min over opposing  o in c of w(o, h)       (1 if none)
    L_c(h) = S_c(h) * A_c(h)

posterior:
    log P(h) = log P0(h) + T * sum over observed channels c of log L_c(h)
    normalise
```

- `T` is a tempering constant chosen from {0.5, 0.7, 1.0} on the fixture corpus (section 15); default 0.7 until then.
- Taking max of supports and min of againsts separately means an opposing observation is never discarded because a supporting one exists in the same channel.
- There is no per-channel cap; nothing sums, so the largest possible channel effect is one strong observation.
- `agent` evidence is capped at medium, tagged `source: agent`, must name the channel it replaces, and can be retracted.

**Initial global priors** (hand-set, replaced per repo as outcomes accrue): this_change 0.55, flaky 0.18, environment 0.12, external 0.10, pre_existing 0.05.

## 9. Confidence reporting

Returned on every response:

- `mode`, `mode_confidence` (high, medium, low), `mode_runner_up` when the margin is small.
- `origin`: list of `{origin, probability}` sorted descending.
- `origin_confidence`: `high` if top ≥ 0.75 and margin over second ≥ 0.35; `medium` if top ≥ 0.50; `low` otherwise. Provisional thresholds, to be set on the corpus.
- `margin`: top minus second.
- `channels_observed`: which origin channels contributed. An empty list means the origin is just the prior.
- `calibration`: `uncalibrated` until section 14 has enough outcomes; then `calibrated` with Brier score and sample size.
- `evidence_added` on every call; the full `evidence_trail` only from `get_investigation`.

Entropy is kept in the trail for calibration work but not surfaced as a field; agents do not act on it.

## 10. Next-action ranking

For an action `a` that discriminates the origin set `D(a)` with current belief `P`:

```
M      = sum over h in D(a) of P(h)                 # mass the action can move
split  = 1 - max over h in D(a) of P(h) / M         # 0 when that mass is already settled
voi(a) = M * split
score  = voi(a) / cost(a)
```

| Action | Channel it fills | Discriminates | Cost |
|---|---|---|---|
| `fetch_sibling_jobs` | matrix_pattern | environment, flaky vs this_change | 0 (already in run data) |
| `fetch_attempts` | attempts, rerun_result | flaky vs deterministic | 0 |
| `fetch_annotations` | annotations | refines mode and file references | 1 |
| `fetch_duration_baseline` | duration | timeout/hang vs others | 1 |
| `fetch_pr_history` | pr_history | this_change vs flaky | 2 |
| `fetch_pr_diff` | diff_overlap | this_change vs flaky vs external; pre_existing | 3 |
| `fetch_base_history` | base_history | pre_existing, flaky vs this_change | 3 |
| `check_status_page` | status_page | external | 1 |
| `fetch_full_step_log` | log_signature | refines mode when the excerpt was inconclusive | 1 |
| `compare_with_last_green` | diff_overlap (base-to-head) | what changed since the last success when there is no PR | 3 |

Actions with cost 0 run automatically inside `start_investigation`. The top three remaining by score are returned as `next_actions`.

**Overrides.** When the top origin is `this_change` and `diff_overlap` has not been observed, `fetch_pr_diff` is always first: the agent needs the diff to fix anything, whatever the score says. When mode is `unknown`, `fetch_full_step_log` is always first.

**Stop rule.** `recommendation = "stop"` only when all of these hold: `mode_confidence` is not low; at least two origin channels observed, at least one of them not `matrix_pattern` or `attempts`; `origin_confidence` is high, or the best remaining score is below 0.05. Otherwise `recommendation = "gather"` with the top action named. The agent is free to ignore it.

**Action-equivalence shortcut.** If the two most likely origins map to the same suggested action in the fix matrix (section 5), `recommendation = "stop"` regardless of origin confidence, with `stop_reason = "action_equivalent"`. A compile error is fixed the same way whether it came from this change or from an unpinned dependency, and the diff the agent fetches to fix it tells it which. This is what makes the stop rule reachable on default-branch pushes and first pushes of a PR, where PR history and often the diff channel are unavailable.

## 11. Tool contracts

All tools return `content` (markdown for reading) and `structured_content` (typed object; schema published as the tool's `outputSchema`).

### `start_investigation`

Input: `repo` (owner/name or URL); exactly one of `run_id`, `job_id`, `pr_number`, `branch`, `sha`; optional `max_excerpt_chars` (default 6000), `max_jobs` (default 8).

Target resolution: `run_id` and `job_id` are exact. `pr_number`, `branch`, and `sha` select **completed, failed** runs for the head SHA. If exactly one failed run exists it is investigated. If several exist, the response lists them as `candidate_runs[]` with workflow name, conclusion, and failed job count, and investigates none; the agent calls again with a `run_id`. If none exist, the response says so and lists the latest runs and their status.

Behaviour: lists jobs for the latest attempt, picks failed jobs, downloads up to `max_jobs` logs prioritised by distinct job-name prefixes so each matrix family is represented, slices each to its failed step (section 13), runs parsers, clusters failed jobs by signature, runs all cost-0 actions, computes mode and origin, ranks next actions.

Output: `investigation_id` (self-describing: `owner/repo#run_id@attempt`), `run` (id, url, workflow path, head sha, branch, PR numbers, attempt, event), `clusters[]` each with `signature`, `representative_job` (job id, name, failed step, failing command, exit code, `errors[]`, `failed_tests[]`, `files[]`, `log_excerpt`), `other_jobs[]` (ids and names sharing the signature), `jobs_not_downloaded[]`, then `mode`, `mode_confidence`, `origin`, `origin_confidence`, `margin`, `channels_observed`, `suggested_actions[]` from the fix matrix, `next_actions[]`, `recommendation`, `evidence_added[]`, `calibration`.

### `gather_evidence`

Input: `investigation_id`, `action` (section 10), optional `job_id` for `fetch_full_step_log`.

Output: `evidence_added[]`, `origin_before`, `origin_after`, `mode` (may change after `fetch_full_step_log` or `fetch_annotations`), `channels_observed`, `next_actions`, `recommendation`, plus the fetched payload: `changed_files[]`, `sibling_jobs[]`, `attempts[]`, `pr_runs[]`, `base_runs[]`, `annotations[]`, `log_excerpt`, or `incidents[]`.

### `add_evidence`

Input: `investigation_id`, `channel` (required; one of section 6), `observation` (free text), `supports` and `against` as `{origin_or_mode: strong|medium|weak}`, optional `log_lines`.

Behaviour: replaces any existing `agent` observation in that channel rather than stacking. Weight capped at medium. Output as `gather_evidence` plus `evidence_id`.

### `retract_evidence`

Input: `investigation_id`, `evidence_id`. Removes an `agent` observation and recomputes. Server-gathered evidence cannot be retracted.

### `get_investigation`

Input: `investigation_id`. Output: full state including `evidence_trail`, `calibration` detail, and `linked_investigations[]` (other attempts of the same run).

### `record_outcome`

Input: `investigation_id`, `actual_mode`, `actual_origin`, `fix_summary`; optional `fix_commit`, `fix_files[]`, `resolved_by` (`code_fix`, `test_fix`, `rerun`, `config_fix`, `dependency_fix`, `ignored`).

Behaviour: stores the outcome with a snapshot of the evidence and the `channels_observed` flags. When `fix_commit` or `resolved_by` is given, the server derives a label from it (a plain rerun that passed → flaky or external; a commit touching only test files → this_change) and stores both the derived and the agent-supplied label, with the derived one preferred for calibration. Output: acknowledgement and current calibration stats.

### `analyze_log`

Input: `log_text`, optional `hint` (step name or command), `max_excerpt_chars`.

Behaviour: stateless. Runs the `log_signature` channel only, returns mode with confidence, errors, failed tests, files, excerpt, and the fix-matrix suggestion for origin `this_change` marked as an assumption. Works for logs from any CI system or a local run.

### Prompt: `fix_ci_failure`

Start the investigation; while the recommendation is `gather`, perform the top action; act on the suggested action for the top (mode, origin); after CI is green, record the outcome with the fix commit.

## 12. Output budget and limits

- One `log_excerpt` per signature cluster, never per job. Default 6000 characters each; total excerpt budget 20000 characters per response, trimmed from the least confident cluster first.
- Logs larger than 20 MB are processed as the failed-step window plus the last 5000 lines; the response says so.
- At most `max_jobs` logs downloaded per investigation (default 8); the rest are listed in `jobs_not_downloaded[]` and can be fetched with `fetch_full_step_log`.
- `errors[]` capped at 30 per cluster, `failed_tests[]` at 50, `files[]` at 25; counts of the remainder are reported.
- `evidence_trail` only from `get_investigation`.

## 13. Log slicing and fallback

The failed step is located by timestamp window from the job's step metadata. This is fragile: timestamps have one-second resolution, skipped steps have null times, composite actions and post-steps blur boundaries, and the real error sometimes prints in an earlier step.

Rules:

1. Slice to the failed step's window with 1.5 s slack.
2. Verify the slice contains an `##[error]` line or the `Process completed with exit code` line. If not, widen to the previous step; if still not, use the whole log.
3. Always run the parsers on the whole log as well (pre-filtered lines only). If the whole-log mode differs from the slice mode and has a higher weight class, report the whole-log mode. The response exposes `slice: {window: failed_step | widened | whole_log, mismatch: bool}`.
4. The excerpt is centred on the anchor line of the winning parser, not on the step boundary.

## 14. State, persistence, and calibration data

- SQLite at `~/.ci-diagnosis-mcp/state.db` (override `CI_DIAGNOSIS_STATE`), WAL mode, 5 s busy timeout, file mode 0600.
- `investigation_id` is `owner/repo#run_id@attempt`, so a lost database never strands an agent: the server rebuilds the investigation from GitHub on demand.
- A rerun is a new attempt and therefore a new investigation, linked to the previous one; the link is what fills the `attempts` and `rerun_result` channels.
- Tables: `investigations` (id, repo, run, attempt, created, mode json, origin json, status), `evidence` (investigation, channel, source, observation, weights json, log lines, created), `channels_observed` (investigation, channel, observed_at), `outcomes` (investigation, actual mode, actual origin, derived label, agent label, resolved_by, fix files json, evidence snapshot json, created), `repo_priors` (repo, origin, count).
- Cached logs live under the state directory, 7-day TTL, 2 GB cap, 0600. Secret masking is GitHub's; the cache is local only.

**Passive outcome inference.** Agents forget to call `record_outcome`, or the session ends before CI is green. So outcomes are also inferred: when `start_investigation` or a scheduled check sees a later run on the same PR or branch that succeeded, and an open investigation exists for an earlier failed run, the server derives a label from what happened between the two SHAs. No new commits and a passing rerun → `flaky` or `external`. Commits touching only test files → `this_change`. Commits touching the workflow → `environment`. Commits touching the lockfile or manifest → `this_change` or `external` depending on whether the manifest changed. The derived label is stored with `source: inferred` and a confidence, and counts for calibration at reduced weight.

**Calibration.**

- Phase 1 (launch): hand weights, `calibration = uncalibrated`. Per-repo origin priors start from day one as a count table with the global prior as the smoothing base.
- Phase 2 (≥ 300 outcomes, ≥ 10 per cell): learn `P(observation | origin)` by counting only over investigations where that observation's channel was observed, which removes the selection bias of agents fetching history only when they suspect flakiness. Replace a hand weight only where the cell meets the sample threshold. Prefer derived labels over agent labels. Report Brier score and a reliability table (predicted band vs observed accuracy). Choose `T` from {0.5, 0.7, 1.0} by Brier on a held-out fifth.
- Phase 3 (≥ 1000): isotonic calibration of the top-origin probability.

## 15. Fixture corpus

- Sources: failed jobs from public repositories, plus synthetic logs for rare modes (OOM, runner lost, fail-fast cancellation).
- Layout: `tests/fixtures/<case>/job.log`, `job.json` (steps and metadata), `run.json` (sibling jobs, attempts), optional `pr_files.json`, `expected.json` (`mode`, `origin`, key errors, failed tests, files, `slice_ok`).
- Coverage for v1: at least two cases per mode and per parser; at least five multi-job runs for the matrix channel; at least three cases where the slice is wrong, to exercise section 13.
- Metrics tracked in CI for this repo: mode accuracy, `unknown` rate (target below 15 percent), origin top-1 accuracy where `expected.json` has an origin, and excerpt-contains-anchor rate.
- The corpus is the regression suite for parsers, the tuning set for `T` and the band thresholds, and the held-out set for calibration.

## 16. Module layout (Python, `mcp` SDK 2.x)

```
ci_diagnosis_mcp/
  server.py                  MCPServer, tool and prompt registration, main()
  investigation.py           Lifecycle: start, gather, add, retract, record; target resolution; clustering
  belief/
    mode.py                  Mode candidates, parser aggregation, line ownership, mode confidence
    origin.py                Origin set, priors, weights table, update rule, bands
    fixes.py                 Fix matrix (mode, origin) -> suggested actions
  actions.py                 VOI ranking, overrides, stop rule
  state.py                   SQLite persistence, log cache, repo priors
  github/
    client.py                httpx wrapper: runs, jobs, attempts, logs (manual redirect), annotations, PR files, PR runs, base runs, status page
    models.py                TypedDicts for the REST shapes we read
  analysis/
    normalize.py             ANSI/timestamp stripping, workflow commands, noise filter, rendering with original line numbers
    slicing.py               Step window, verification, widening, whole-log cross-check (section 13)
    files.py                 File-reference extraction and path normalisation
    excerpt.py               Focused excerpt around the anchor within a character budget
    parsers/                 One module per ecosystem: regexes, observations with weights and claimed ranges
    channels.py              Builds the cost-0 channels from run and job metadata
tests/
  fixtures/                  Section 15 corpus
  test_parsers.py, test_mode.py, test_origin.py, test_actions.py, test_slicing.py, test_github_client.py (respx-mocked)
```

Parsers are the only part most contributors touch: one file, a few regexes, a function from lines to observations, and a fixture.

## 17. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Wrong step slice makes every downstream field wrong | Section 13: verify, widen, whole-log cross-check, `slice_mismatch` flag |
| Parser coverage on the long tail (Nx, Turbo, Bazel, Gradle, custom reporters) | Strong generic fallback; `unknown` rate tracked on the corpus; one-file parser contract |
| Agents read the excerpt and ignore the belief, leaving the origin layer as dead weight | Evaluate time-to-green on fixture cases with and without `next_actions` before investing in calibration |
| Label echo in `record_outcome` | Derived labels from fix commits and reruns preferred over agent labels |
| Selection bias in learned weights | Count only where the channel was observed |
| Output too large for context | Section 12 budget |
| Lost state | Self-describing ids; rebuild from GitHub |

## 18. Decisions

Resolved:

1. `code_regression` vs `stale_test`: merged into origin `this_change`; the fix matrix tells the agent how to attribute using the diff.
2. `T` and caps: no cap; `T` chosen on the corpus from a grid, default 0.7 until then.
3. `rerun_job`: not in v1; rerun results enter through attempt linking.
4. Per-repo priors: from day one, count table with global fallback.

Still open:

1. Band thresholds in section 9 are provisional until the corpus exists.
2. `status_page` channel: GitHub's status API is public, but registry status pages (npm, PyPI, Docker Hub) vary; decide whether v1 includes only GitHub.
3. Whether `analyze_log` should accept a `job.json` alongside the text so local users of other CI systems can get `step_context` and slicing too.

## 19. Performance and reachability

Bottlenecks found in review and how the implementation handles them.

| Bottleneck | Where | Handling |
|---|---|---|
| Serial log downloads, up to 8 × 20 MB | `start_investigation` | Download concurrently with a limit of 4; stream each log straight into the disk cache; report progress through the MCP context after each job so clients do not time out |
| Parsing cost: 15 parsers × every line, twice | sections 8, 13 | Parse one log at a time, never all in memory; pre-filter lines with one cheap "interesting line" regex (error keywords, workflow commands, test-runner markers, exit codes) and feed parsers only those lines plus a window around them; the whole-log cross-check always runs, but only over the pre-filtered candidate lines, so it costs a fraction of the slice parse; compile all patterns once at import |
| Memory: line objects for large logs | `normalize.py` | Keep raw lines as a list of strings and build `LogLine` objects lazily for the filtered subset; hard cap of 20 MB per log as in section 12 |
| History actions needing logs of other runs | section 6 | Signature levels: level 1 from the jobs API, level 2 from at most one extra log per action |
| Stop rule unreachable without PR history or diff | section 10 | Action-equivalence shortcut |
| Outcomes never recorded | section 14 | Passive outcome inference |
| Blocking I/O stalls other tool calls | `server.py` | Async `httpx` client; CPU-bound parsing in a worker thread |
| Rate limits | `github/client.py` | Token required (unauthenticated GitHub allows 60 requests per hour, which is unusable); conditional requests with ETags for run and job listings; a budget of at most 40 API calls per `start_investigation` |

Targets, checked by the benchmarks in section 20:

- `start_investigation` with 4 failed jobs of 10 MB each: p95 under 20 s on a laptop with a normal connection.
- `analyze_log` on a 10 MB log: under 2 s.
- Peak resident memory under 500 MB for the 8 × 20 MB worst case.
- At most 40 GitHub API calls per investigation including all cost-0 actions.

## 20. Evaluation

Five layers, from cheapest to most meaningful. The first three run in this repository's own CI; the last two run on demand.

### 20.1 Parser and mode accuracy (offline, every commit)

On the fixture corpus (section 15):

- Mode accuracy and a mode confusion matrix. Gate: ≥ 90 percent correct, `unknown` rate ≤ 15 percent.
- Per-parser precision and recall on the cases that belong to it, so a new parser cannot steal matches from an existing one unnoticed.
- Error extraction: fraction of expected `errors[]` (file, line, message) found; fraction of expected `failed_tests[]` found. Gate: ≥ 85 percent each.
- Excerpt quality: the excerpt contains the expected anchor line in ≥ 95 percent of cases, and its size stays within budget.
- Slice correctness: `slice_ok` from `expected.json` vs the slicer's own verification result; the three deliberately broken cases must be caught by the fallback.

### 20.2 Origin accuracy and evidence efficiency (offline, every commit)

On the subset of corpus cases with a known origin and recorded channels:

- Origin top-1 accuracy and Brier score after all available channels are applied.
- Band reliability: within each `origin_confidence` band, the observed accuracy must not be lower than the band implies (high ≥ 0.75, medium ≥ 0.50).
- Evidence efficiency: replay each case following `next_actions` greedily and record the number of actions and total cost until `stop`. Compare against a fixed order (diff, then history, then the rest) and a random order. The ranking must beat the fixed order on mean cost to a correct stop; otherwise it is not earning its complexity.
- Stop safety: fraction of cases where `stop` was recommended with the wrong top origin. Gate: ≤ 5 percent.
- Tempering and band thresholds are chosen on a training half of these cases and reported on the other half; both numbers are shown so overfitting is visible.

### 20.3 Latency, memory, and API budget (offline, every commit)

Replay recorded GitHub API traffic (respx cassettes) for a benchmark set of runs with 1, 4, and 8 failed jobs and logs of 1, 10, and 20 MB:

- p50 and p95 wall time of `start_investigation` and of each `gather_evidence` action.
- Parse throughput in lines per second, with and without the pre-filter.
- Peak resident memory.
- API calls per investigation, counted by the mock.

Gates are the targets in section 19. Results are written to a JSON file and compared against the previous commit so regressions show as a diff, not just a pass or fail.

### 20.4 Agent end-to-end benchmark (on demand, before releases)

The question that matters: does a coding agent fix CI faster and more correctly with the server than without it?

- Benchmark set: 30 to 50 real repositories forked with an injected, labelled breakage each, covering every mode and origin at least twice. Examples: a type error in one file, a stale snapshot, a lockfile out of sync, a workflow referencing a missing secret, a flaky test seeded with a timing race, a dependency pinned to a yanked version.
- Two arms, same agent and model: with the MCP server configured, and without it (the agent uses `gh` and reads raw logs itself). A third arm with the server but with `next_actions` hidden measures whether the origin layer adds value beyond the excerpt.
- Metrics per case: CI green within the budget (success), wall time to green, agent tool calls, input and output tokens, and wrong-fix rate, defined as changing source to make a flaky or pre-existing failure pass.
- Report: success rate, median time, median tokens, and wrong-fix rate per arm, with per-mode breakdowns. The server earns a release when it improves success or time without raising the wrong-fix rate.

### 20.5 Production telemetry (continuous, local, opt-in export)

Stored in the state database and summarised by a `ci-diagnosis-mcp stats` command:

- `unknown` rate, stop-reached rate, mean actions per investigation, and the override rate (how often the agent's next call was not the recommended action).
- Calibration drift: Brier score over the last 100 outcomes vs the previous 100.
- p95 latency per tool and cache hit rate.
- Outcome sources: fraction recorded by agents vs inferred passively.

These numbers decide when phase 2 of calibration (section 14) is switched on and whether the action ranking is being used at all.

