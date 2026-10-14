"""T6: tool and prompt listing (AC-6), analyze_log, errors, token hygiene (AC-12 tool half), stdout purity and determinism (AC-15), progress."""
import json
import os
import pathlib
import subprocess
import sys

import httpx
import pytest
import respx
from mcp.client.client import Client

from ci_diagnosis_mcp import server as srv
from ci_diagnosis_mcp.github.client import GitHub
from tests.fake_github import mk_job, mk_run

FIX = pathlib.Path(__file__).parent / "data"
TOKEN = "ghp_VERYSECRET_TOKEN_12345"
START_KEYS = {"investigation_id", "run", "clusters", "jobs_not_downloaded", "mode", "mode_confidence", "mode_runner_up", "mode_margin", "origin", "origin_confidence", "margin", "channels_observed",
              "suggested_actions", "next_actions", "unavailable_actions", "recommendation", "stop_reason", "evidence_added", "calibration"}
ANALYZE_KEYS = {"mode", "mode_confidence", "errors", "failed_tests", "files", "log_excerpt", "slice", "suggested_actions", "assumed_origin"}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setenv("CI_DIAGNOSIS_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(srv, "_state", None)
    yield tmp_path / "state"


async def test_lists_exactly_the_spec_tools_and_prompt_with_required_keys():
    async with Client(srv.server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        prompts = [p.name for p in (await c.list_prompts()).prompts]
    assert set(tools) == {"start_investigation", "gather_evidence", "get_investigation", "record_outcome", "analyze_log"}
    assert prompts == ["fix_ci_failure"]
    for name in tools:
        assert tools[name].output_schema and tools[name].output_schema.get("properties"), name
    assert START_KEYS <= set(tools["start_investigation"].output_schema["required"])
    assert ANALYZE_KEYS <= set(tools["analyze_log"].output_schema["required"])
    run_props = tools["start_investigation"].output_schema["$defs"]["RunView"]["required"]
    assert {"id", "url", "workflow_path", "head_sha", "head_branch", "attempt", "event", "pull_requests"} <= set(run_props)
    job_props = tools["start_investigation"].output_schema["$defs"]["JobView"]["required"]
    assert {"job_id", "name", "url", "failed_step", "failing_command", "exit_code", "errors", "failed_tests", "files", "log_excerpt", "slice"} <= set(job_props)


async def test_analyze_log_is_structured_deterministic_and_capped():
    log = (FIX / "tsc-errors" / "job.log").read_text()
    async with Client(srv.server) as c:
        a = await c.call_tool("analyze_log", {"log_text": log, "hint": "npm run typecheck"})
        b = await c.call_tool("analyze_log", {"log_text": log, "hint": "npm run typecheck"})
        big = await c.call_tool("analyze_log", {"log_text": "x" * (21 * 1024 * 1024)})
    assert not a.is_error and a.structured_content["mode"] == "compile" and a.structured_content["errors"][0]["code"] == "TS2322"
    assert "untrusted log data" in a.content[0].text and "```" in a.content[0].text
    assert json.dumps(a.structured_content, sort_keys=True) == json.dumps(b.structured_content, sort_keys=True)
    assert big.is_error and "20 MB" in big.content[0].text


async def test_unknown_investigation_and_bad_action_are_actionable_errors(monkeypatch):
    async with Client(srv.server) as c:
        r = await c.call_tool("get_investigation", {"investigation_id": "acme/app#1@1"})
        o = await c.call_tool("record_outcome", {"investigation_id": "acme/app#1@1", "actual_mode": "compile", "actual_origin": "this_change", "fix_summary": "x"})
    assert r.is_error and "start_investigation" in r.content[0].text and o.is_error


def _mock_github(m: respx.MockRouter, run: dict, jobs: list[dict], logs: dict[int, str]) -> None:
    base = "https://api.github.com"
    m.get(f"{base}/repos/acme/app/actions/runs/{run['id']}").respond(200, json=run)
    m.get(f"{base}/repos/acme/app/actions/runs/{run['id']}/jobs").respond(200, json={"jobs": jobs})
    for jid, text in logs.items():
        m.get(f"{base}/repos/acme/app/actions/jobs/{jid}/logs").respond(302, headers={"location": f"https://blob.example.com/{jid}"})
        m.get(f"https://blob.example.com/{jid}").respond(200, text=text)
    m.get(f"{base}/repos/acme/app/pulls/7/files").respond(200, json=[{"filename": "src/cart.ts", "status": "modified", "changes": 3}])


async def test_full_flow_over_mcp_never_exposes_token_and_reports_progress(monkeypatch, isolated_state, caplog):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(srv, "_gh", lambda: _real_client())
    run = mk_run(100)
    j1, l1 = mk_job(1000, "test (node 18)", "jest-assert", 100)
    j2, l2 = mk_job(1001, "test (node 20)", "jest-assert", 100)
    progress = []

    async def on_progress(p, total, message):
        progress.append((p, total, message))

    with respx.mock(assert_all_called=False) as m:
        _mock_github(m, run, [j1, j2], {1000: l1, 1001: l2})
        async with Client(srv.server) as c:
            s = await c.call_tool("start_investigation", {"repo": "acme/app", "run_id": 100}, progress_callback=on_progress)
            g = await c.call_tool("gather_evidence", {"investigation_id": s.structured_content["investigation_id"], "action": "fetch_pr_diff"})
            i = await c.call_tool("get_investigation", {"investigation_id": s.structured_content["investigation_id"]})
            o = await c.call_tool("record_outcome", {"investigation_id": s.structured_content["investigation_id"], "actual_mode": "test_assertion", "actual_origin": "this_change", "fix_summary": "fixed", "fix_files": ["src/cart.ts"]})
        blob_auth = [req.headers.get("authorization") for req, _ in m.calls if "blob.example.com" in str(req.url)]
    assert not s.is_error and s.structured_content["mode"] == "test_assertion" and len(s.structured_content["clusters"]) == 1
    assert len(progress) >= 2 and all(t == 2 for _, t, _ in progress)
    assert not g.is_error and "diff_overlap" in g.structured_content["channels_observed"]
    assert not i.is_error and i.structured_content["evidence_trail"] and not o.is_error and o.structured_content["derived_origin"] == "this_change"
    assert blob_auth and all(a is None for a in blob_auth)
    err = caplog.text  # pytest routes the stderr handler's records here; production logging goes to stderr (see the stdio test)
    assert "start_investigation repo=acme/app" in err  # capture is live, so the token check below is not vacuous
    assert "blob.example.com" not in err  # httpx request logging (signed URLs) is off
    everything = "\n".join(json.dumps(r.structured_content) + "".join(x.text for x in r.content) for r in (s, g, i, o)) + err
    assert TOKEN not in everything
    for p in isolated_state.rglob("*"):
        if p.is_file():
            assert TOKEN.encode() not in p.read_bytes(), p


async def _real_client() -> GitHub:
    return GitHub(os.environ["GITHUB_TOKEN"], base_url="https://api.github.com")


async def test_candidate_runs_is_an_error_with_structured_candidates(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(srv, "_gh", lambda: _real_client())
    r1, r2 = mk_run(100), mk_run(101, workflow_id=12, name="Lint")
    with respx.mock(assert_all_called=False) as m:
        m.get("https://api.github.com/repos/acme/app/pulls/7").respond(200, json={"number": 7, "head": {"sha": "abc123", "ref": "f"}, "base": {"ref": "main"}})
        m.get("https://api.github.com/repos/acme/app/actions/runs").respond(200, json={"workflow_runs": [r1, r2]})
        async with Client(srv.server) as c:
            r = await c.call_tool("start_investigation", {"repo": "acme/app", "pr_number": 7})
    assert r.is_error and r.structured_content is None and "run_id 100" in r.content[0].text and "run_id 101" in r.content[0].text


def test_stdio_stdout_contains_only_jsonrpc_frames(tmp_path):
    env = {**os.environ, "CI_DIAGNOSIS_STATE": str(tmp_path / "s"), "PYTHONUNBUFFERED": "1"}
    log = (FIX / "pytest-assert" / "job.log").read_text()
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "analyze_log", "arguments": {"log_text": log}}},
    ]
    proc = subprocess.run([sys.executable, "-m", "ci_diagnosis_mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True, env=env, timeout=60)
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    assert lines, proc.stderr
    frames = [json.loads(l) for l in lines]
    assert all(f.get("jsonrpc") == "2.0" for f in frames)
    result = next(f for f in frames if f.get("id") == 2)["result"]
    assert result["structuredContent"]["mode"] == "test_assertion" and "starting on stdio" in proc.stderr
