"""Origin posterior (DESIGN §8, SPEC FR-8): per channel max of supports × min of againsts; product across channels; tempering."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..analysis.types import WEIGHT_RATIO

ORIGINS = ("this_change", "pre_existing", "flaky", "environment", "external")
GLOBAL_PRIOR = {"this_change": 0.55, "flaky": 0.18, "environment": 0.12, "external": 0.10, "pre_existing": 0.05}
T = 0.7
CHANNELS = ("matrix_pattern", "attempts", "pr_history", "base_history", "diff_overlap", "annotations", "agent")


@dataclass(slots=True)
class Evidence:
    channel: str
    observation: str
    weights: dict[str, str]  # origin -> weight key (supports or againsts, by ratio)
    source: str = "server"
    log_lines: list[int] = field(default_factory=list)

    @property
    def ratios(self) -> dict[str, float]:
        return {o: WEIGHT_RATIO[w] for o, w in self.weights.items()}


def posterior(evidence: list[Evidence], prior: dict[str, float] | None = None, t: float = T) -> dict[str, float]:
    prior = prior or GLOBAL_PRIOR
    logp = {o: math.log(prior[o]) for o in ORIGINS}
    by_channel: dict[str, list[Evidence]] = {}
    for e in evidence:
        by_channel.setdefault(e.channel, []).append(e)
    for items in by_channel.values():
        for o in ORIGINS:
            sup = max((e.ratios.get(o, 1.0) for e in items if e.ratios.get(o, 1.0) > 1), default=1.0)
            agn = min((e.ratios.get(o, 1.0) for e in items if e.ratios.get(o, 1.0) < 1), default=1.0)
            logp[o] += t * math.log(sup * agn)
    z = max(logp.values())
    w = {o: math.exp(v - z) for o, v in logp.items()}
    s = sum(w.values())
    return {o: round(v / s, 4) for o, v in sorted(w.items(), key=lambda kv: -kv[1])}


def channels_observed(evidence: list[Evidence]) -> list[str]:
    return sorted({e.channel for e in evidence})


def confidence(post: dict[str, float]) -> tuple[str, float]:
    ranked = sorted(post.values(), reverse=True)
    top, second = ranked[0], ranked[1] if len(ranked) > 1 else 0.0
    margin = round(top - second, 4)
    band = "high" if top >= 0.75 and margin >= 0.35 else "medium" if top >= 0.50 else "low"
    return band, margin


# ---- Observation builders for the channels the server gathers (DESIGN §7 origin table) ----

def matrix_evidence(total_jobs: int, failed_jobs: int, same_signature: bool, axis_differs: bool) -> list[Evidence]:
    if total_jobs <= 1:
        return []
    if failed_jobs == total_jobs and same_signature:
        return [Evidence("matrix_pattern", f"all {total_jobs} matrix jobs failed with the same signature", {"this_change": "medium", "pre_existing": "weak", "flaky": "against"})]
    if failed_jobs < total_jobs:
        return [Evidence("matrix_pattern", f"{failed_jobs} of {total_jobs} matrix jobs failed" + (" on a differing axis" if axis_differs else ""), {"environment": "strong", "flaky": "medium", "this_change": "against"})]
    return [Evidence("matrix_pattern", f"all {total_jobs} jobs failed with different signatures", {"this_change": "weak"})]


def attempts_evidence(other_attempts: list[dict]) -> list[Evidence]:
    """other_attempts: [{attempt, conclusion, same_signature}] for attempts other than the one under investigation (FR-9)."""
    out = []
    for a in other_attempts:
        if a["conclusion"] == "success":
            out.append(Evidence("attempts", f"attempt {a['attempt']} of the same run passed", {"flaky": "strong", "external": "medium", "this_change": "strong_against"}))
        elif a.get("same_signature"):
            out.append(Evidence("attempts", f"attempt {a['attempt']} failed with the same level-1 signature", {"flaky": "strong_against", "this_change": "medium", "pre_existing": "medium"}))
        else:
            out.append(Evidence("attempts", f"attempt {a['attempt']} failed with a different signature", {"flaky": "medium"}))
    return out


def pr_history_evidence(earlier_pushes: list[dict]) -> list[Evidence]:
    """earlier_pushes: [{sha, conclusion, same_signature}] for earlier head SHAs of the PR."""
    if not earlier_pushes:
        return []
    if all(p["conclusion"] != "success" and p.get("same_signature") for p in earlier_pushes):
        return [Evidence("pr_history", f"same failure on all {len(earlier_pushes)} earlier pushes of this PR", {"this_change": "strong", "pre_existing": "medium", "flaky": "strong_against"})]
    if earlier_pushes[0]["conclusion"] == "success":
        return [Evidence("pr_history", "previous push of this PR was green; failure appeared on the latest push", {"this_change": "strong"})]
    return [Evidence("pr_history", "earlier pushes failed differently", {"flaky": "weak", "this_change": "weak"})]


def base_history_evidence(base_same_signature: bool, base_green: bool, flaky_count: int, window: int) -> list[Evidence]:
    if base_same_signature:
        return [Evidence("base_history", "base branch fails the same job with the same signature", {"pre_existing": "strong", "this_change": "strong_against"})]
    if base_green and flaky_count >= 2:
        return [Evidence("base_history", f"base branch green now, but this job failed {flaky_count} times in the last {window} base runs", {"flaky": "strong"})]
    if base_green:
        return [Evidence("base_history", f"base branch green and this job did not fail in the last {window} base runs", {"this_change": "weak", "flaky": "against"})]
    return []


def diff_evidence(mode: str, changed: list[str], referenced: list[str], failing_tests_files: list[str]) -> list[Evidence]:
    """changed: PR files; referenced: files named by errors; failing_tests_files: files of failing tests."""
    out = []
    cs = set(changed)
    lock = any(f.endswith(("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "Pipfile.lock", "go.sum", "Cargo.lock")) for f in cs)
    manifest = any(f.endswith(("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "Pipfile")) for f in cs)
    workflow = any(f.startswith(".github/workflows/") for f in cs)
    tests_only = bool(cs) and all(_is_test(f) for f in cs)
    hit = [f for f in referenced + failing_tests_files if f in cs]
    if hit:
        out.append(Evidence("diff_overlap", f"files named by the failure are in the diff: {', '.join(hit[:5])}", {"this_change": "strong"}))
    elif tests_only and failing_tests_files:
        out.append(Evidence("diff_overlap", "only test files changed and those tests fail", {"this_change": "strong"}))
    elif cs and (referenced or failing_tests_files):
        out.append(Evidence("diff_overlap", "no file referenced by the failure is in the diff", {"this_change": "against", "flaky": "medium", "external": "medium"}))
    if mode in ("dependency", "compile") and (lock or manifest):
        out.append(Evidence("diff_overlap", "lockfile or dependency manifest changed in the diff", {"this_change": "strong"}))
    elif mode == "dependency" and cs and not manifest:
        out.append(Evidence("diff_overlap", "dependency failure with no manifest change in the diff", {"external": "strong"}))
    if workflow and mode == "config":
        out.append(Evidence("diff_overlap", "workflow file changed and the failure is a config error", {"this_change": "strong"}))
    return out


def _is_test(path: str) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(path, g) for g in ("tests/*", "tests/**", "test/*", "test/**", "spec/*", "spec/**", "*/__tests__/*", "**/__tests__/**", "**/test_*.py", "test_*.py", "**/*_test.*", "*_test.*", "**/*.test.*", "*.test.*", "**/*.spec.*", "*.spec.*"))


TEST_GLOBS_DOC = "tests/**, test/**, spec/**, **/__tests__/**, **/test_*.py, **/*_test.*, **/*.test.*, **/*.spec.*"
