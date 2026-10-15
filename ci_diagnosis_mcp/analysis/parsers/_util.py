from __future__ import annotations

import re
from dataclasses import dataclass

from ..types import ExtractedError, FailedTest, LogLine, Observation


@dataclass(slots=True)
class ParseContext:
    step_name: str | None = None

    @property
    def step_kind(self) -> str:
        s = (self.step_name or "").lower()
        if any(k in s for k in ("checkout", "setup-", "install", "npm ci", "pip install", "poetry install", "uv sync", "pnpm install", "yarn install", "bundle install")):
            return "install"
        if any(k in s for k in ("test", "pytest", "jest", "vitest", "spec", "e2e")):
            return "test"
        if any(k in s for k in ("lint", "format", "prettier", "eslint", "ruff", "black", "flake8", "mypy", "pyright", "typecheck", "tsc", "clippy")):
            return "lint"
        if any(k in s for k in ("build", "compile", "docker")):
            return "build"
        if "deploy" in s or "publish" in s or "release" in s:
            return "deploy"
        return "other"


def find_all(lines: list[LogLine], pattern: re.Pattern[str]) -> list[tuple[LogLine, re.Match[str]]]:
    out = []
    for l in lines:
        m = pattern.search(l.text)
        if m:
            out.append((l, m))
    return out


def find_first(lines, pattern):
    hits = find_all(lines, pattern)
    return hits[0] if hits else None


def find_last(lines, pattern):
    hits = find_all(lines, pattern)
    return hits[-1] if hits else None


def pos_of(lines: list[LogLine], index: int) -> int:
    for i, l in enumerate(lines):
        if l.index == index:
            return i
    return 0


def following(lines: list[LogLine], pos: int, limit: int = 8, stop: re.Pattern[str] | None = None, blanks_ok: bool = False) -> list[str]:
    """Non-empty lines after `pos`, stopping at a workflow command or `stop`; also at a blank after content unless blanks_ok."""
    out: list[str] = []
    for l in lines[pos + 1:]:
        t = l.text
        if not t.strip():
            if out and not blanks_ok:
                break
            continue
        if t.startswith("##[") or (stop and stop.search(t)):
            break
        out.append(t.strip())
        if len(out) >= limit:
            break
    return out


def trunc(s: str, n: int = 300) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def dedupe_errors(errors: list[ExtractedError]) -> list[ExtractedError]:
    seen, out = set(), []
    for e in errors:
        k = (e.file, e.line, e.message)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def dedupe_tests(tests: list[FailedTest]) -> list[FailedTest]:
    seen, out = set(), []
    for t in tests:
        k = (t.file, t.name)
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def obs(mode: str, weight: str, parser: str, summary: str, hits: list[LogLine], *, anchor: int | None = None, errors=(), tests=(), signals=(), hint="") -> Observation:
    idx = [l.index for l in hits]
    claimed = (min(idx), max(idx)) if idx else (0, 0)
    return Observation(mode, weight, parser, summary, anchor if anchor is not None else claimed[0], claimed, list(errors), list(tests), list(signals), hint)


def plural(n: int, w: str) -> str:
    return f"{n} {w}{'' if n == 1 else 's'}"
