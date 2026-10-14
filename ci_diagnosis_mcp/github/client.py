"""Async GitHub REST client: token resolution, GHES base URL, call counting, actionable errors (SPEC FR-19)."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

import httpx

from ..state import validate_repo

SCOPES_DOC = "actions:read, contents:read, pull_requests:read, checks:read"


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None, hint: str = ""):
        super().__init__(message + (f" ({hint})" if hint else ""))
        self.status, self.hint = status, hint


def resolve_token() -> str | None:
    """GITHUB_TOKEN, then GH_TOKEN, then `gh auth token`."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def parse_repo(repo: str) -> tuple[str, str]:
    r = re.sub(r"^https?://github\.com/", "", repo.strip()).removesuffix(".git").strip("/")
    try:
        validate_repo(r)
    except ValueError:
        raise GitHubError(f"invalid repo {repo!r}", hint="expected owner/name") from None
    owner, name = r.split("/")
    return owner, name


class GitHub:
    def __init__(self, token: str | None, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        if not token:
            raise GitHubError("no GitHub token", hint="set GITHUB_TOKEN or GH_TOKEN, or run `gh auth login`")
        self.base_url = (base_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        self.calls = 0
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ci-diagnosis-mcp"}
        self._api = httpx.AsyncClient(base_url=self.base_url, headers={**headers, "Authorization": f"Bearer {token}"}, timeout=30, follow_redirects=False, transport=transport)
        self._bare = httpx.AsyncClient(headers={"User-Agent": "ci-diagnosis-mcp"}, timeout=120, follow_redirects=True, transport=transport)  # never carries the token

    async def aclose(self) -> None:
        await self._api.aclose()
        await self._bare.aclose()

    def _raise(self, r: httpx.Response, what: str) -> None:
        try:
            msg = r.json().get("message", "")
        except Exception:
            msg = r.text[:200]
        s = r.status_code
        hint = {401: "token is invalid or expired", 404: "check the id, or the token cannot see this repo", 410: "the log has expired; rerun the job or use another job"}.get(s, "")
        if s == 403 or s == 429:
            hint = "rate limited; wait or use a token with higher limits" if r.headers.get("x-ratelimit-remaining") == "0" else f"insufficient token scope; needs {SCOPES_DOC}"
        raise GitHubError(f"GitHub {s} on {what}: {msg}", s, hint)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        r = await self._api.get(path, params=params)
        if r.status_code >= 400:
            self._raise(r, path)
        return r.json()

    async def paginate(self, path: str, params: dict[str, Any] | None = None, key: str | None = None, limit: int = 300) -> list[Any]:
        out: list[Any] = []
        url: str | None = path
        p = {"per_page": 100, **(params or {})}
        while url and len(out) < limit:
            self.calls += 1
            r = await self._api.get(url, params=p if url == path else None)
            if r.status_code >= 400:
                self._raise(r, path)
            data = r.json()
            out.extend(data[key] if key else data)
            m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("link", ""))
            url = m.group(1) if m else None
        return out[:limit]

    # ---- endpoints -------------------------------------------------------
    async def run(self, owner: str, repo: str, run_id: int) -> dict:
        return await self.get(f"/repos/{owner}/{repo}/actions/runs/{run_id}")

    async def runs(self, owner: str, repo: str, limit: int = 50, **params: Any) -> list[dict]:
        return await self.paginate(f"/repos/{owner}/{repo}/actions/runs", params, "workflow_runs", limit)

    async def workflow_runs(self, owner: str, repo: str, workflow_id: int, limit: int = 20, **params: Any) -> list[dict]:
        return await self.paginate(f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs", params, "workflow_runs", limit)

    async def jobs(self, owner: str, repo: str, run_id: int, attempt: int | None = None) -> list[dict]:
        if attempt:
            return await self.paginate(f"/repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs", None, "jobs", 200)
        return await self.paginate(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", {"filter": "latest"}, "jobs", 200)

    async def job(self, owner: str, repo: str, job_id: int) -> dict:
        return await self.get(f"/repos/{owner}/{repo}/actions/jobs/{job_id}")

    async def job_log(self, owner: str, repo: str, job_id: int, max_bytes: int = 64 * 1024 * 1024) -> str:
        """Plain-text log. GitHub answers 302 to blob storage; the redirect is fetched WITHOUT the token."""
        self.calls += 1
        r = await self._api.get(f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
        if r.status_code in (301, 302, 303, 307):
            self.calls += 1
            async with self._bare.stream("GET", r.headers["location"]) as blob:
                if blob.status_code >= 400:
                    raise GitHubError(f"log download failed with {blob.status_code}", blob.status_code, "the log URL expired; retry")
                chunks, size = [], 0
                async for c in blob.aiter_bytes():
                    chunks.append(c)
                    size += len(c)
                    if size > max_bytes:
                        break
                return b"".join(chunks).decode("utf-8", "replace")
        if r.status_code >= 400:
            self._raise(r, f"job {job_id} logs")
        return r.text

    async def annotations(self, owner: str, repo: str, check_run_id: int) -> list[dict]:
        try:
            return await self.paginate(f"/repos/{owner}/{repo}/check-runs/{check_run_id}/annotations", None, None, 100)
        except GitHubError as e:
            if e.status in (403, 404):
                return []
            raise

    async def pull(self, owner: str, repo: str, number: int) -> dict:
        return await self.get(f"/repos/{owner}/{repo}/pulls/{number}")

    async def pull_files(self, owner: str, repo: str, number: int) -> list[dict]:
        return await self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/files", None, None, 300)

    async def pull_commits(self, owner: str, repo: str, number: int) -> list[dict]:
        return await self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/commits", None, None, 100)
