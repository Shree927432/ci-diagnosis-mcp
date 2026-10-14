"""Next-action ranking, availability, overrides and stop rule (DESIGN §10, SPEC FR-11)."""
from __future__ import annotations

from dataclasses import dataclass

from .fixes import action_signature

# action -> (channel, discriminated origins, cost)
ACTIONS = {
    "fetch_sibling_jobs": ("matrix_pattern", ("environment", "flaky", "this_change"), 0),
    "fetch_attempts": ("attempts", ("flaky", "this_change", "pre_existing"), 0),
    "fetch_annotations": ("annotations", (), 1),
    "fetch_pr_history": ("pr_history", ("this_change", "flaky"), 2),
    "fetch_pr_diff": ("diff_overlap", ("this_change", "flaky", "external", "pre_existing"), 3),
    "fetch_base_history": ("base_history", ("pre_existing", "flaky", "this_change"), 3),
    "fetch_full_step_log": ("log_signature", (), 1),
}
FREE = ("matrix_pattern", "attempts")


@dataclass(slots=True)
class Context:
    mode: str
    mode_confidence: str
    posterior: dict[str, float]
    origin_confidence: str
    channels_observed: list[str]
    taken: set[str]
    has_pr: bool
    job_count: int
    attempt: int
    attempt_count: int


def availability(ctx: Context) -> dict[str, str | None]:
    """action -> None if available, else the reason it is not."""
    out: dict[str, str | None] = {}
    for a in ACTIONS:
        if a in ctx.taken:
            out[a] = "already taken"
        elif a in ("fetch_pr_diff", "fetch_pr_history") and not ctx.has_pr:
            out[a] = "run is not associated with a pull request"
        elif a == "fetch_sibling_jobs" and ctx.job_count <= 1:
            out[a] = "run has a single job"
        elif a == "fetch_attempts" and ctx.attempt_count <= 1:
            out[a] = "run has a single attempt"
        else:
            out[a] = None
    return out


def voi(action: str, post: dict[str, float]) -> float:
    _, discr, cost = ACTIONS[action]
    if not discr:
        return 0.02 / max(cost, 1)  # refinement actions: small constant value
    m = sum(post.get(o, 0.0) for o in discr)
    if m <= 0:
        return 0.0
    split = 1 - max(post.get(o, 0.0) for o in discr) / m
    return m * split / max(cost, 1)


def rank(ctx: Context) -> tuple[list[dict], list[dict]]:
    avail = availability(ctx)
    scored = [{"action": a, "channel": ACTIONS[a][0], "cost": ACTIONS[a][2], "score": round(voi(a, ctx.posterior), 4)} for a, why in avail.items() if why is None]
    scored.sort(key=lambda d: (-d["score"], d["cost"]))
    top = max(ctx.posterior, key=ctx.posterior.get)
    # Overrides (FR-11)
    if ctx.mode == "unknown" and "fetch_full_step_log" in {d["action"] for d in scored}:
        scored.sort(key=lambda d: d["action"] != "fetch_full_step_log")
    elif top == "this_change" and "diff_overlap" not in ctx.channels_observed and "fetch_pr_diff" in {d["action"] for d in scored}:
        scored.sort(key=lambda d: d["action"] != "fetch_pr_diff")
    unavailable = [{"action": a, "reason": why} for a, why in avail.items() if why and why != "already taken"]
    return scored[:3], unavailable


def recommendation(ctx: Context, ranked: list[dict]) -> tuple[str, str]:
    """Returns (recommendation, stop_reason). Precedence per FR-11."""
    ranked_origins = sorted(ctx.posterior.items(), key=lambda kv: -kv[1])
    top = ranked_origins[0][0]
    second = ranked_origins[1][0] if len(ranked_origins) > 1 else top
    if ctx.mode_confidence != "low" and action_signature(ctx.mode, top) == action_signature(ctx.mode, second):
        return "stop", "action_equivalent"
    if ctx.mode_confidence != "low":
        non_free = [c for c in ctx.channels_observed if c not in FREE]
        if len(ctx.channels_observed) >= 2 and non_free and (ctx.origin_confidence == "high" or not ranked or ranked[0]["score"] < 0.05):
            return "stop", "origin_confident" if ctx.origin_confidence == "high" else "no_informative_action"
        if not ranked:
            return "stop", "no_actions_available"
    if not ranked:
        return "stop", "no_actions_available"
    return "gather", ranked[0]["action"]
