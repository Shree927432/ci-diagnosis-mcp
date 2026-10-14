"""Derived outcome label (SPEC FR-15), ordered rules, first match wins."""
from __future__ import annotations

from .origin import _is_test


def derive_origin(resolved_by: str | None, fix_files: list[str], workflow_change_observed: bool) -> str | None:
    files = [f for f in fix_files if f]
    if resolved_by == "rerun" and not files:
        return "flaky"
    if files and all(_is_test(f) for f in files):
        return "this_change"
    if any(f.startswith(".github/workflows/") for f in files):
        return "this_change" if workflow_change_observed else "environment"
    if files:
        return "this_change"
    return None
