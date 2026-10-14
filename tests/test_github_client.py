import json
import pathlib

import httpx
import pytest
import respx

from ci_diagnosis_mcp.github import client as gh
from ci_diagnosis_mcp.github.client import GitHub, GitHubError, parse_repo, resolve_token

TOKEN = "ghp_SECRET_TOKEN_abc123"


def test_token_resolution_order(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "a")
    monkeypatch.setenv("GH_TOKEN", "b")
    assert resolve_token() == "a"
    monkeypatch.delenv("GITHUB_TOKEN")
    assert resolve_token() == "b"
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.setattr(gh.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "c\n"})())
    assert resolve_token() == "c"
    monkeypatch.setattr(gh.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert resolve_token() is None


def test_missing_token_is_actionable():
    with pytest.raises(GitHubError, match="gh auth login"):
        GitHub(None)


def test_parse_repo():
    assert parse_repo("https://github.com/acme/app.git") == ("acme", "app")
    for bad in ("../x", "a/b/c", "a", ""):
        with pytest.raises(GitHubError):
            parse_repo(bad)


@pytest.mark.asyncio
async def test_api_url_override_changes_host(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
    g = GitHub(TOKEN)
    with respx.mock(assert_all_called=True) as m:
        m.get("https://ghe.example.com/api/v3/repos/a/b/actions/runs/1").respond(200, json={"id": 1})
        assert (await g.run("a", "b", 1))["id"] == 1
    await g.aclose()


@pytest.mark.asyncio
async def test_job_log_redirect_never_sends_token_to_blob_host_and_counts_calls():
    g = GitHub(TOKEN, base_url="https://api.github.com")
    seen = {}

    def blob(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text="2026-10-15T10:00:00.0000000Z hello\n")

    with respx.mock() as m:
        m.get("https://api.github.com/repos/a/b/actions/jobs/7/logs").respond(302, headers={"location": "https://blob.example.com/x?sig=1"})
        m.get("https://blob.example.com/x").mock(side_effect=blob)
        text = await g.job_log("a", "b", 7)
    assert "hello" in text and seen["auth"] is None and g.calls == 2
    await g.aclose()


@pytest.mark.asyncio
async def test_errors_are_actionable():
    g = GitHub(TOKEN, base_url="https://api.github.com")
    with respx.mock() as m:
        m.get("https://api.github.com/repos/a/b/actions/runs/1").respond(404, json={"message": "Not Found"})
        m.get("https://api.github.com/repos/a/b/actions/runs/2").respond(403, json={"message": "limit"}, headers={"x-ratelimit-remaining": "0"})
        m.get("https://api.github.com/repos/a/b/actions/runs/3").respond(403, json={"message": "Resource not accessible"})
        m.get("https://api.github.com/repos/a/b/actions/jobs/4/logs").respond(410, json={"message": "Gone"})
        for rid, needle in ((1, "cannot see"), (2, "rate limited"), (3, "scope")):
            with pytest.raises(GitHubError, match=needle):
                await g.run("a", "b", rid)
        with pytest.raises(GitHubError, match="expired"):
            await g.job_log("a", "b", 4)
    await g.aclose()


@pytest.mark.asyncio
async def test_pagination_follows_link_header():
    g = GitHub(TOKEN, base_url="https://api.github.com")
    with respx.mock() as m:
        m.get("https://api.github.com/repos/a/b/actions/runs/1/jobs", params={"page": "2"}).respond(200, json={"jobs": [{"id": 2}]})
        m.get("https://api.github.com/repos/a/b/actions/runs/1/jobs").respond(200, json={"jobs": [{"id": 1}]}, headers={"link": '<https://api.github.com/repos/a/b/actions/runs/1/jobs?page=2>; rel="next"'})
        jobs = await g.jobs("a", "b", 1)
    assert [j["id"] for j in jobs] == [1, 2] and g.calls == 2
    await g.aclose()


def test_direct_dependencies_are_only_mcp_and_httpx():
    text = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
    deps = [l.strip().strip('",') for l in text.split("dependencies = [", 1)[1].split("]", 1)[0].splitlines() if l.strip()]
    assert sorted(d.split(">")[0].split("<")[0] for d in deps) == ["httpx", "mcp"] and any(d.startswith("mcp>=2") for d in deps)


@pytest.mark.asyncio
async def test_api_request_carries_token_but_offhost_link_is_not_followed():
    g = GitHub(TOKEN, base_url="https://api.github.com")
    seen = []

    def api(request):
        seen.append(("api", request.headers.get("authorization")))
        return httpx.Response(200, json={"jobs": [{"id": 1}]}, headers={"link": '<https://evil.example/x?page=2>; rel="next"'})

    def evil(request):
        seen.append(("evil", request.headers.get("authorization")))
        return httpx.Response(200, json={"jobs": [{"id": 2}]})

    with respx.mock(assert_all_called=False) as m:
        m.get("https://api.github.com/repos/a/b/actions/runs/1/jobs").mock(side_effect=api)
        m.get("https://evil.example/x").mock(side_effect=evil)
        jobs = await g.jobs("a", "b", 1)
    assert [j["id"] for j in jobs] == [1] and seen == [("api", f"Bearer {TOKEN}")]
    await g.aclose()


@pytest.mark.asyncio
async def test_token_whitespace_is_stripped_and_transport_errors_never_leak_it(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", f"  {TOKEN}\n")
    assert resolve_token() == TOKEN
    g = GitHub(f"{TOKEN}\n".strip(), base_url="https://api.github.com")
    with respx.mock() as m:
        m.get("https://api.github.com/repos/a/b/actions/runs/1").mock(side_effect=httpx.ConnectError("boom " + TOKEN))
        with pytest.raises(GitHubError) as ei:
            await g.run("a", "b", 1)
    assert TOKEN not in str(ei.value) and "ConnectError" in str(ei.value)
    await g.aclose()


@pytest.mark.asyncio
async def test_moved_rate_limit_and_308_and_tail_cap():
    g = GitHub(TOKEN, base_url="https://api.github.com")
    with respx.mock() as m:
        m.get("https://api.github.com/repos/a/b/actions/runs/1").respond(301, json={"message": "Moved Permanently"})
        m.get("https://api.github.com/repos/a/b/actions/runs/2").respond(429, json={"message": "slow down"})
        m.get("https://api.github.com/repos/a/b/actions/runs/3").respond(403, json={"message": "secondary"}, headers={"retry-after": "30", "x-ratelimit-remaining": "99"})
        m.get("https://api.github.com/repos/a/b/actions/jobs/4/logs").respond(308, headers={"location": "/blob/4"})
        m.get("https://api.github.com/blob/4").respond(200, content=b"head\n" + b"x" * 50 + b"\ntail-error\n")
        with pytest.raises(GitHubError, match="moved"):
            await g.run("a", "b", 1)
        for rid in (2, 3):
            with pytest.raises(GitHubError, match="rate limited"):
                await g.run("a", "b", rid)
        text = await g.job_log("a", "b", 4, max_bytes=20)
    assert text.startswith("##[notice]ci-diagnosis: log truncated") and text.endswith("tail-error\n") and "head" not in text
    await g.aclose()


def test_pyproject_pins_mcp_major_and_python():
    text = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
    assert '"mcp>=2,<3"' in text and 'requires-python = ">=3.10"' in text
