import pytest

from ci_diagnosis_mcp import investigation as inv
from ci_diagnosis_mcp.github.client import GitHubError
from ci_diagnosis_mcp.state import State
from tests.fake_github import FakeGitHub, mk_job, mk_run

pytestmark = pytest.mark.asyncio


@pytest.fixture
def state(tmp_path):
    s = State(tmp_path)
    yield s
    s.close()


def matrix_run(run_id=100, *, failed=("jest-assert",) * 3 + ("tsc-errors",) * 2, passed=2, attempt=1, prs=(7,), event="pull_request"):
    """A run with len(failed) failed jobs (fixture names) plus `passed` green jobs, all in one job family."""
    jobs, logs = [], {}
    for i, fx in enumerate(failed):
        family = fx.split("-")[0] if len(set(failed)) > 1 else "test"
        j, log = mk_job(1000 + i, f"{family} (node {18 + i})", fx, run_id)
        jobs.append(j)
        logs[j["id"]] = log
    for i in range(passed):
        j, _ = mk_job(2000 + i, f"test (node {30 + i})", None, run_id, conclusion="success")
        jobs.append(j)
    run = mk_run(run_id, attempt=attempt, prs=prs, event=event)
    return run, jobs, logs


async def test_start_by_run_id_clusters_by_signature_and_gathers_free_channels(state):
    run, jobs, logs = matrix_run()
    gh = FakeGitHub(runs=[run], jobs={(100, 1): jobs}, logs=logs)
    r = await inv.start(gh, state, "acme/app", run_id=100)
    assert r["investigation_id"] == "acme/app#100@1" and r["mode"] == "test_assertion" and r["mode_confidence"] == "high"
    assert len(r["clusters"]) == 2 and {c["mode"] for c in r["clusters"]} == {"test_assertion", "compile"}
    assert r["clusters"][0]["job_count"] + r["clusters"][1]["job_count"] == 5 and r["jobs_not_downloaded"] == []
    assert "matrix_pattern" in r["channels_observed"] and r["channels_observed"] == ["matrix_pattern"]
    assert r["next_actions"][0]["action"] == "fetch_pr_diff" and r["recommendation"] == "gather"
    assert r["unavailable_actions"] == [{"action": "fetch_attempts", "reason": "run has a single attempt"}]
    for key in ("origin", "origin_confidence", "margin", "suggested_actions", "stop_reason", "calibration", "slice", "evidence_added"):
        assert key in r
    assert r["clusters"][0]["representative_job"]["log_excerpt"].startswith("```")
    # resume returns the stored investigation with the trail
    again = await inv.start(gh, state, "acme/app", run_id=100)
    assert again["investigation_id"] == r["investigation_id"] and "evidence_trail" in again


async def test_budget_12_failed_jobs(state):
    run, jobs, logs = matrix_run(failed=("jest-assert",) * 5 + ("tsc-errors",) * 4 + ("ruff-errors",) * 3, passed=0)
    gh = FakeGitHub(runs=[run], jobs={(100, 1): jobs}, logs=logs)
    r = await inv.start(gh, state, "acme/app", run_id=100)
    assert len(gh.log_requests) == 8 and len(r["jobs_not_downloaded"]) == 4
    assert len(r["clusters"]) == 3 and len({c["signature"] for c in r["clusters"]}) == 3
    assert sum(len(c["representative_job"]["log_excerpt"]) for c in r["clusters"]) <= 20000
    assert gh.calls <= 40


async def test_api_budget_with_8_failed_jobs_attempt_2(state):
    run, jobs, logs = matrix_run(failed=("pytest-assert",) * 8, passed=0, attempt=2)
    prev, _ = mk_job(999, "test (node 18)", "pytest-assert", 100)
    gh = FakeGitHub(runs=[run], jobs={(100, 2): jobs, (100, 1): [prev]}, logs=logs)
    r = await inv.start(gh, state, "acme/app", run_id=100)
    assert gh.calls <= 40, gh.calls
    assert r["attempt_count"] == 2 and "attempts" in r["channels_observed"]
    assert any("same level-1 signature" in e["observation"] for e in r["evidence_added"])


async def test_candidate_runs_and_single_run_resolution(state):
    r1, j1, l1 = matrix_run(100, failed=("jest-assert",), passed=0)
    r2, j2, l2 = matrix_run(101, failed=("tsc-errors",), passed=0)
    r2["name"], r2["workflow_id"] = "Lint", 12
    pulls = {7: {"number": 7, "head": {"sha": "abc123", "ref": "feature"}, "base": {"ref": "main"}}}
    gh = FakeGitHub(runs=[r1, r2], jobs={(100, 1): j1, (101, 1): j2}, logs={**l1, **l2}, pulls=pulls)
    r = await inv.start(gh, state, "acme/app", pr_number=7)
    assert "investigation_id" not in r and [c["run_id"] for c in r["candidate_runs"]] == [100, 101]
    gh2 = FakeGitHub(runs=[r1], jobs={(100, 1): j1}, logs=l1, pulls=pulls)
    r = await inv.start(gh2, state, "acme/app", pr_number=7)
    assert r["investigation_id"] == "acme/app#100@1"
    gh3 = FakeGitHub(runs=[mk_run(5, status="in_progress", conclusion=None)])
    r = await inv.start(gh3, state, "acme/app", run_id=5)
    assert "in_progress" in r["error"]
    gh4 = FakeGitHub(runs=[mk_run(6, conclusion="startup_failure")], jobs={})
    r = await inv.start(gh4, state, "acme/app", run_id=6)
    assert r["mode"] == "config" and "ci.yml" in r["summary"]
    with pytest.raises(GitHubError):
        await inv.start(gh4, state, "acme/app")


async def test_gather_pr_diff_then_stop(state):
    run, jobs, logs = matrix_run(failed=("jest-assert",) * 3, passed=0)
    pulls = {7: {"number": 7, "head": {"sha": "abc123", "ref": "feature"}, "base": {"ref": "main"}}}
    files = {7: [{"filename": "src/cart.ts", "status": "modified", "changes": 4}, {"filename": "src/cart.test.ts", "status": "modified", "changes": 2}]}
    gh = FakeGitHub(runs=[run], jobs={(100, 1): jobs}, logs=logs, pulls=pulls, pull_files=files)
    r = await inv.start(gh, state, "acme/app", run_id=100)
    g = await inv.gather(gh, state, r["investigation_id"], "fetch_pr_diff")
    assert g["origin_after"]["this_change"] > g["origin_before"]["this_change"]
    assert "diff_overlap" in g["channels_observed"] and g["changed_files"][0]["path"] == "src/cart.ts"
    assert g["recommendation"] == "stop" and g["stop_reason"] in ("origin_confident", "action_equivalent", "no_informative_action")
    full = inv.get(state, r["investigation_id"])
    assert [e["channel"] for e in full["evidence_trail"]] == ["matrix_pattern", "diff_overlap"] and full["taken"] == ["fetch_sibling_jobs", "fetch_pr_diff"]


async def test_gather_base_history_and_full_log(state):
    run, jobs, logs = matrix_run(failed=("pytest-assert",), passed=0, prs=(), event="push")
    base_fail = mk_run(90, sha="base1", branch="feature", event="push", prs=(), created="2026-10-14T10:00:00Z")
    bj, bl = mk_job(900, "test (node 18)", "pytest-assert", 90)
    gh = FakeGitHub(runs=[run, base_fail], jobs={(100, 1): jobs, (90, 1): [bj]}, logs={**logs, 900: bl})
    r = await inv.start(gh, state, "acme/app", run_id=100)
    assert {u["action"] for u in r["unavailable_actions"]} >= {"fetch_pr_diff", "fetch_pr_history"} and "fetch_sibling_jobs" in r["taken"]
    g = await inv.gather(gh, state, r["investigation_id"], "fetch_base_history")
    assert any(e["channel"] == "base_history" and "same signature" in e["observation"] for e in g["evidence_added"])
    assert g["origin_after"]["pre_existing"] > g["origin_before"]["pre_existing"]
    g = await inv.gather(gh, state, r["investigation_id"], "fetch_full_step_log", job_id=1000)
    assert g["log_excerpt"].startswith("```") and "fetch_full_step_log" in inv.get(state, r["investigation_id"])["taken"]
    with pytest.raises(GitHubError):
        await inv.gather(gh, state, r["investigation_id"], "nope")


async def test_record_outcome_rules(state):
    run, jobs, logs = matrix_run(failed=("jest-assert",), passed=0)
    gh = FakeGitHub(runs=[run], jobs={(100, 1): jobs}, logs=logs)
    r = await inv.start(gh, state, "acme/app", run_id=100)
    i = r["investigation_id"]
    assert inv.record_outcome(state, i, "test_assertion", "flaky", "rerun passed", resolved_by="rerun")["derived_origin"] == "flaky"
    assert inv.record_outcome(state, i, "test_assertion", "this_change", "fixed test", fix_files=["tests/test_a.py"])["derived_origin"] == "this_change"
    assert inv.record_outcome(state, i, "config", "environment", "perms", fix_files=[".github/workflows/ci.yml"])["derived_origin"] == "environment"
    assert inv.record_outcome(state, i, "test_assertion", "this_change", "mixed", fix_files=["tests/test_a.py", "src/a.py"])["derived_origin"] == "this_change"
    o = inv.record_outcome(state, i, "test_assertion", "flaky", "ignored", resolved_by="ignored")
    assert o["derived_origin"] is None and o["outcomes_recorded"] == 5
    with pytest.raises(inv.NotFound):
        inv.get(state, "acme/app#1@1")


async def test_matrix_evidence_only_within_the_failed_jobs_family(state):
    # heterogeneous jobs: one 'typing' failure beside unrelated green jobs -> no matrix evidence
    run = mk_run(300, prs=(), event="push")
    typing, log = mk_job(3000, "typing", "mypy-errors", 300)
    others = [mk_job(3100 + i, name, None, 300, conclusion="success")[0] for i, name in enumerate(("Mac", "3.12", "3.13", "Windows"))]
    gh = FakeGitHub(runs=[run], jobs={(300, 1): [typing] + others}, logs={3000: log})
    r = await inv.start(gh, state, "acme/app", run_id=300)
    assert r["mode"] == "compile" and r["channels_observed"] == [] and r["evidence_added"] == []
    # true matrix: one of three 'test (…)' shards fails -> environment/flaky evidence
    run2, jobs, logs = matrix_run(301, failed=("jest-assert",), passed=2, prs=(), event="push")
    gh2 = FakeGitHub(runs=[run2], jobs={(301, 1): jobs}, logs=logs)
    r2 = await inv.start(gh2, state, "acme/app", run_id=301)
    assert r2["channels_observed"] == ["matrix_pattern"] and r2["evidence_added"][0]["weights"]["environment"] == "strong"
