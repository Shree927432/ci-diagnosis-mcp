"""File references from logs, normalised to repo-relative paths (FR-5)."""
from __future__ import annotations

import re

from .types import ExtractedError, FileRef, LogLine

_RUNNER_PREFIX = re.compile(r"^(?:/home/runner/work/[^/]+/[^/]+/|/__w/[^/]+/[^/]+/|[A-Za-z]:[\\/]a[\\/][^\\/]+[\\/][^\\/]+[\\/])")
_IGNORED = re.compile(
    r"(^|/)(node_modules|site-packages|dist-packages|\.cargo|\.rustup|\.gradle|\.m2|\.nvm|\.pyenv|\.venv|venv|__pycache__|\.cache|\.npm|\.yarn|go/pkg/mod|hostedtoolcache|_actions|_tool)/"
)
_ABS_SYSTEM = re.compile(r"^/(usr|opt|etc|lib|proc|sys|tmp|var)/")
PATH = re.compile(
    r"(?<![\w@/])((?:/|\.{1,2}/)?(?:[\w.@-]+/)*[\w.@-]+\.(?:[cm]?[jt]sx?|py|pyi|go|rs|rb|java|kt|kts|scala|swift|c|cc|cpp|h|hpp|cs|php|ex|exs|vue|svelte|json|ya?ml|toml|xml|html?|css|scss|sql|sh|tf|proto|md|lock|cfg|ini))"
    r"(?::(\d+)(?::(\d+))?|\((\d+),(\d+)\)|\",\s*line\s+(\d+))?"
)


def normalize_path(p: str) -> str:
    p = p.replace("\\", "/")
    p = _RUNNER_PREFIX.sub("", p)
    return p[2:] if p.startswith("./") else p


def interesting(p: str) -> bool:
    return bool(p) and not _IGNORED.search(p) and not _ABS_SYSTEM.match(p) and not p.startswith("http") and ":" not in p.split("/")[0][:5]


def collect_file_refs(lines: list[LogLine], errors: list[ExtractedError], limit: int = 25) -> list[FileRef]:
    refs: dict[str, FileRef] = {}

    def bump(path: str, line: int | None, col: int | None) -> None:
        r = refs.get(path)
        if r:
            r.mentions += 1
            if r.line is None and line is not None:
                r.line, r.column = line, col
        else:
            refs[path] = FileRef(path, line, col)

    for e in errors:
        if e.file:
            p = normalize_path(e.file)
            if interesting(p):
                bump(p, e.line, e.column)
    for l in lines:
        for m in PATH.finditer(l.text):
            p = normalize_path(m.group(1))
            if not interesting(p):
                continue
            line = m.group(2) or m.group(4) or m.group(6)
            col = m.group(3) or m.group(5)
            bump(p, int(line) if line else None, int(col) if col else None)
    return sorted(refs.values(), key=lambda r: (-r.mentions, r.line or 0))[:limit]
