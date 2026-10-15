"""Async GitHub REST client: token resolution, GHES base URL, call counting, actionable errors (SPEC FR-19)."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ..state import validate_repo

SCOPES_DOC = "actions:read, contents:read, pull_requests:read, checks:read"


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None, hint: str = ""):
        super().__init__(message + (f" ({hint})" if hint else ""))
        self.status, self.hint = status, hint


def resolve_token() -> str | None:
    """GITHUB_TOKEN, then GH_TOKEN, then `gh auth token` (for the GITHUB_API_URL host when set)."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    cmd = ["gh", "auth", "token"]
    api = os.environ.get("GITHUB_API_URL")
    if api and urlparse(api).hostname and urlparse(api).hostname != "api.github.com":
        cmd += ["--hostname", str(urlparse(api).hostname)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
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

    @staticmethod
    def _is_rate_limit(r: httpx.Response) -> bool:
        return r.status_code == 429 or (r.status_code == 403 and (r.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in r.headers))

    def _raise(self, r: httpx.Response, what: str) -> None:
        try:
            msg = str(r.json().get("message", ""))[:200]
        except Exception:
            msg = r.text[:200]
        s = r.status_code
        if 300 <= s < 400:
            raise GitHubError(f"GitHub {s} on {what}: resource moved", s, "the repository was renamed or transferred; use its current owner/name")
        hint = {401: "token is invalid or expired", 404: "check the id, or the token cannot see this repo", 410: "the log has expired; rerun the job or use another job"}.get(s, "")
        if self._is_rate_limit(r):
            hint = "rate limited; wait or use a token with higher limits"
        elif s == 403:
            hint = f"insufficient token scope; needs {SCOPES_DOC}"
        raise GitHubError(f"GitHub {s} on {what}: {msg}", s, hint)

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, **kw: Any) -> httpx.Response:
        """All HTTP goes through here so transport errors never surface raw messages (which could carry headers)."""
        self.calls += 1
        try:
            return await client.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub request failed: {type(e).__name__}", None, "check network access, GITHUB_API_URL, and that the token has no stray whitespace") from None

    def _same_host(self, url: str) -> bool:
        a, b = urlparse(url), urlparse(self.base_url)
        return (a.scheme, a.netloc) == (b.scheme, b.netloc)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        r = await self._request(self._api, "GET", path, params=params)
        if r.status_code >= 300:
            self._raise(r, path)
        return r.json()

    async def paginate(self, path: str, params: dict[str, Any] | None = None, key: str | None = None, limit: int = 300) -> list[Any]:
        out: list[Any] = []
        url: str | None = path
        p = {"per_page": 100, **(params or {})}
        while url and len(out) < limit:
            r = await self._request(self._api, "GET", url, params=p if url == path else None)
            if r.status_code >= 300:
                self._raise(r, path)
            data = r.json()
            out.extend(data[key] if key else data)
            m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("link", ""))
            url = m.group(1) if m else None
            if url and not self._same_host(url):
                break  # never follow a Link to another host with the token
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
        """Plain-text log. GitHub answers 302 to blob storage; the redirect is fetched WITHOUT the token.
        Logs over max_bytes keep their tail and start with a visible truncation notice."""
        path = f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        r = await self._request(self._api, "GET", path)
        if r.status_code in (301, 302, 303, 307, 308):
            location = urljoin(self.base_url + path, r.headers.get("location", ""))
            if not location or location == self.base_url + path:
                raise GitHubError(f"GitHub redirect without Location on {path}", r.status_code)
            self.calls += 1
            try:
                async with self._bare.stream("GET", location) as blob:
                    if blob.status_code >= 400:
                        raise GitHubError(f"log download failed with {blob.status_code}", blob.status_code, "the log URL expired; retry")
                    buf, dropped = bytearray(), 0
                    async for c in blob.aiter_bytes():
                        buf += c
                        if len(buf) > 2 * max_bytes:  # bound memory while streaming; keep the tail
                            dropped += len(buf) - max_bytes
                            del buf[: len(buf) - max_bytes]
            except httpx.HTTPError as e:
                raise GitHubError(f"log download failed: {type(e).__name__}", None, "retry the download") from None
            if len(buf) > max_bytes:
                dropped += len(buf) - max_bytes
                del buf[: len(buf) - max_bytes]
            text = bytes(buf).decode("utf-8", "replace")
            return (f"##[notice]ci-diagnosis: log truncated, first {dropped} bytes dropped\n" + text) if dropped else text
        if r.status_code >= 300:
            self._raise(r, f"job {job_id} logs")
        body = r.content
        if len(body) > max_bytes:
            return f"##[notice]ci-diagnosis: log truncated, first {len(body) - max_bytes} bytes dropped\n" + body[-max_bytes:].decode("utf-8", "replace")
        return body.decode("utf-8", "replace")

    async def annotations(self, owner: str, repo: str, check_run_id: int) -> list[dict]:
        try:
            return await self.paginate(f"/repos/{owner}/{repo}/check-runs/{check_run_id}/annotations", None, None, 100)
        except GitHubError as e:
            if e.status == 404 or (e.status == 403 and "scope" in e.hint):
                return []
            raise

    async def pull(self, owner: str, repo: str, number: int) -> dict:
        return await self.get(f"/repos/{owner}/{repo}/pulls/{number}")

    async def pulls_for_head(self, owner: str, repo: str, head: str) -> list[dict]:
        """Open PRs whose head is `user:branch`. Finds fork PRs, which /commits/{sha}/pulls does not list."""
        return await self.get(f"/repos/{owner}/{repo}/pulls", {"head": head, "state": "open"})

    async def pull_files(self, owner: str, repo: str, number: int) -> list[dict]:
        return await self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/files", None, None, 300)

    async def pull_commits(self, owner: str, repo: str, number: int) -> list[dict]:
        return await self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/commits", None, None, 100)
