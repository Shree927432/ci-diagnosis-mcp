"""In-memory stand-in for GitHub with the same async surface and call accounting as github.client.GitHub."""
from __future__ import annotations

import json
import pathlib

FIX = pathlib.Path(__file__).parent / "fixtures"
T0 = "2026-10-15T10:00:"


def fixture(name: str) -> tuple[str, dict]:
    return (FIX / name / "job.log").read_text(), json.loads((FIX / name / "job.json").read_text())


class FakeGitHub:
    def __init__(self, *, runs: list[dict] | None = None, jobs: dict[tuple[int, int], list[dict]] | None = None, logs: dict[int, str] | None = None,
                 pulls: dict[int, dict] | None = None, pull_files: dict[int, list[dict]] | None = None, annotations: dict[int, list[dict]] | None = None):
        self.calls = 0
        self._runs = runs or []
        self._jobs = jobs or {}  # (run_id, attempt) -> jobs
        self._logs = logs or {}
        self._pulls = pulls or {}
        self._pull_files = pull_files or {}
        self._annotations = annotations or {}
        self.log_requests: list[int] = []

    def _run(self, run_id):
        return next(r for r in self._runs if r["id"] == run_id)

    async def run(self, owner, repo, run_id):
        self.calls += 1
        return self._run(run_id)

    async def runs(self, owner, repo, limit=50, **params):
        self.calls += 1
        out = self._runs
        if params.get("head_sha"):
            out = [r for r in out if r["head_sha"] == params["head_sha"]]
        if params.get("branch"):
            out = [r for r in out if r["head_branch"] == params["branch"]]
        if params.get("event"):
            out = [r for r in out if r["event"] == params["event"]]
        return out[:limit]

    async def workflow_runs(self, owner, repo, workflow_id, limit=20, **params):
        self.calls += 1
        out = [r for r in self._runs if r["workflow_id"] == workflow_id]
        if params.get("branch"):
            out = [r for r in out if r["head_branch"] == params["branch"]]
        return out[:limit]

    async def jobs(self, owner, repo, run_id, attempt=None):
        self.calls += 1
        run = self._run(run_id)
        return list(self._jobs.get((run_id, attempt or run["run_attempt"]), []))

    async def job(self, owner, repo, job_id):
        self.calls += 1
        for jobs in self._jobs.values():
            for j in jobs:
                if j["id"] == job_id:
                    return j
        raise KeyError(job_id)

    async def job_log(self, owner, repo, job_id, max_bytes=0):
        self.calls += 2  # API call + blob redirect, as the real client counts
        self.log_requests.append(job_id)
        log = self._logs[job_id]
        if isinstance(log, Exception):
            raise log
        return log() if callable(log) else log  # callables let perf tests generate big logs lazily

    async def annotations(self, owner, repo, check_run_id):
        self.calls += 1
        return list(self._annotations.get(check_run_id, []))

    async def pull(self, owner, repo, number):
        self.calls += 1
        return self._pulls[number]

    async def pull_files(self, owner, repo, number):
        self.calls += 1
        return list(self._pull_files.get(number, []))

    async def pull_commits(self, owner, repo, number):
        self.calls += 1
        return []


def mk_run(run_id: int, *, attempt=1, conclusion="failure", status="completed", sha="abc123", branch="feature", event="pull_request", prs=(7,), workflow_id=11, created="2026-10-15T10:00:00Z", name="CI") -> dict:  # noqa: E501
    return {"id": run_id, "name": name, "path": ".github/workflows/ci.yml", "workflow_id": workflow_id, "run_attempt": attempt, "status": status, "conclusion": conclusion, "head_sha": sha, "head_branch": branch,
            "event": event, "html_url": f"https://github.com/acme/app/actions/runs/{run_id}", "created_at": created, "pull_requests": [{"number": n} for n in prs]}


def mk_job(job_id: int, name: str, fixture_name: str | None, run_id: int, *, conclusion="failure") -> tuple[dict, str | None]:
    if fixture_name:
        log, job = fixture(fixture_name)
        job = {**job, "id": job_id, "name": name, "run_id": run_id, "conclusion": conclusion, "html_url": f"https://github.com/acme/app/actions/runs/{run_id}/job/{job_id}"}
        return job, log
    steps = [{"name": "Run tests", "number": 1, "conclusion": "success", "started_at": T0 + "00Z", "completed_at": T0 + "10Z"}]
    return {"id": job_id, "name": name, "run_id": run_id, "conclusion": conclusion, "steps": steps, "html_url": ""}, None
