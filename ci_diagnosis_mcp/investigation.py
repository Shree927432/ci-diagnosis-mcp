"""Investigation lifecycle: resolve target, download and analyse failed jobs, cluster, gather evidence, record outcomes.

Three sections (resolve/download, cluster/channels, gather/outcome) in one module; the server layer only calls
start(), gather(), get() and record_outcome().
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from typing import Any, Awaitable, Callable

from .analysis.normalize import excerpt, parse_lines
from .analysis.pipeline import Analysis, analyze
from .belief import actions as act
from .belief.fixes import suggested_actions
from .belief.origin import (
    Evidence, attempts_evidence, base_history_evidence, channels_observed, confidence, diff_evidence, matrix_evidence, posterior, pr_history_evidence,
)
from .belief.outcome import derive_origin
from .github.client import GitHub, GitHubError, parse_repo
from .state import State

FAILED_RUN = ("failure", "timed_out", "startup_failure")
FAILED_JOB = ("failure", "timed_out")
EXCERPT_BUDGET = 20000
Progress = Callable[[int, int, str], Awaitable[None]]


class NotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# resolve / download
# ---------------------------------------------------------------------------

def _failed_runs(runs: list[dict]) -> list[dict]:
    done = [r for r in runs if r.get("status") == "completed"]
    failed = [r for r in done if r.get("conclusion") in FAILED_RUN]
    return failed or [r for r in done if r.get("conclusion") == "cancelled"]


def _brief(r: dict) -> dict:
    return {"run_id": r["id"], "workflow": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion"), "head_sha": r.get("head_sha"), "url": r.get("html_url"), "created_at": r.get("created_at")}


async def resolve_run(gh: GitHub, owner: str, name: str, *, run_id=None, job_id=None, pr_number=None, branch=None, sha=None) -> dict:
    """Returns {'run': run} or {'candidate_runs': [...]} or {'error': msg, 'latest_runs': [...]}."""
    if job_id:
        run_id = (await gh.job(owner, name, int(job_id)))["run_id"]
    if run_id:
        run = await gh.run(owner, name, int(run_id))
        if run.get("status") != "completed":
            return {"error": f"run {run_id} is still {run.get('status')}; wait for it to complete", "latest_runs": [_brief(run)]}
        return {"run": run}
    if pr_number:
        sha = (await gh.pull(owner, name, int(pr_number)))["head"]["sha"]
    params = {"head_sha": sha} if sha else {"branch": branch}
    runs = await gh.runs(owner, name, 50, **params)
    failed = _failed_runs(runs)
    if len(failed) == 1:
        return {"run": failed[0]}
    if failed:
        return {"candidate_runs": [_brief(r) for r in failed]}
    return {"error": "no completed failed run found for this target", "latest_runs": [_brief(r) for r in runs[:5]]}


def _failed_jobs(jobs: list[dict]) -> list[dict]:
    failed = [j for j in jobs if j.get("conclusion") in FAILED_JOB]
    return failed or [j for j in jobs if j.get("conclusion") == "cancelled"]


def _prefix(job_name: str) -> str:
    return job_name.split(" (")[0].strip()


def prioritize(failed: list[dict], max_jobs: int) -> tuple[list[dict], list[dict]]:
    """Round-robin over distinct job-name prefixes so every matrix family is represented."""
    groups: dict[str, list[dict]] = {}
    for j in failed:
        groups.setdefault(_prefix(j["name"]), []).append(j)
    chosen: list[dict] = []
    while len(chosen) < max_jobs and any(groups.values()):
        for g in groups.values():
            if g and len(chosen) < max_jobs:
                chosen.append(g.pop(0))
    chosen_ids = {j["id"] for j in chosen}
    return chosen, [j for j in failed if j["id"] not in chosen_ids]


async def download(gh: GitHub, state: State, owner: str, name: str, jobs: list[dict], progress: Progress | None) -> None:
    """Download up to 4 logs concurrently straight into the disk cache; nothing is kept in memory (NFR-3)."""
    sem, done = asyncio.Semaphore(4), 0

    async def one(j: dict) -> None:
        nonlocal done
        async with sem:
            if state.cached_log(j["id"]) is None:
                state.cache_log(j["id"], await gh.job_log(owner, name, j["id"]))
        done += 1
        if progress:
            await progress(done, len(jobs), f"downloaded log for {j['name']}")

    await asyncio.gather(*(one(j) for j in jobs))


# ---------------------------------------------------------------------------
# cluster / channels
# ---------------------------------------------------------------------------

def level1(job: dict) -> str:
    step = next((s["name"] for s in job.get("steps") or [] if s.get("conclusion") in ("failure", "timed_out")), None)
    return f"{_prefix(job['name'])}::{step}"


def signature(a: Analysis) -> str:
    first = a.errors[0].message if a.errors else a.summary
    return f"{a.mode}:{hashlib.sha1(first.encode()).hexdigest()[:10]}"


def _job_view(job: dict, a: Analysis) -> dict:
    return {"job_id": job["id"], "name": job["name"], "url": job.get("html_url"), "failed_step": a.failed_step, "failing_command": a.failing_command, "exit_code": a.exit_code,
            "errors": [asdict(e) for e in a.errors], "failed_tests": [asdict(t) for t in a.failed_tests], "files": [asdict(f) for f in a.files], "log_excerpt": a.log_excerpt,
            "slice": {"window": a.slice_window, "mismatch": a.slice_mismatch}, "summary": a.summary, "root_cause_hint": a.root_cause_hint, "parser": a.parser, "truncated": a.truncated,
            "errors_omitted": max(0, len(a.errors) - 30), "level1_signature": level1(job)}


RANK = {"high": 2, "medium": 1, "low": 0}


def cluster(analysed: list[tuple[dict, Analysis]]) -> list[dict]:
    groups: dict[str, list[tuple[dict, Analysis]]] = {}
    for job, a in analysed:
        groups.setdefault(signature(a), []).append((job, a))
    clusters = []
    for sig, items in groups.items():
        items.sort(key=lambda x: -RANK[x[1].mode_confidence])
        job, a = items[0]
        clusters.append({"signature": sig, "mode": a.mode, "mode_confidence": a.mode_confidence, "representative_job": _job_view(job, a), "other_jobs": [{"job_id": j["id"], "name": j["name"]} for j, _ in items[1:]], "job_count": len(items)})
    clusters.sort(key=lambda c: (-RANK[c["mode_confidence"]], -c["job_count"]))
    used = 0
    for c in clusters:  # FR-20 total excerpt budget; least confident clusters lose their excerpt first
        rep = c["representative_job"]
        if used + len(rep["log_excerpt"]) > EXCERPT_BUDGET:
            rep["log_excerpt"] = f"(excerpt omitted: response budget reached; call gather_evidence fetch_full_step_log with job_id={rep['job_id']})"
        used += len(rep["log_excerpt"])
    return clusters


def _belief(state: State, inv: dict) -> None:
    """Recompute origin, confidence, actions and recommendation from stored evidence; mutate inv in place."""
    ev = [Evidence(e["channel"], e["observation"], e["weights"], e["source"], e["log_lines"]) for e in state.evidence(inv["investigation_id"]) if e["weights"]]
    post = posterior(ev)
    band, margin = confidence(post)
    top = max(post, key=post.get)
    ctx = act.Context(inv["mode"], inv["mode_confidence"], post, band, channels_observed(ev), set(inv["taken"]), inv["has_pr"], inv["job_count"], inv["run"]["attempt"], inv["attempt_count"])
    ranked, unavailable = act.rank(ctx)
    rec, reason = act.recommendation(ctx, ranked)
    inv.update(origin=[{"origin": o, "probability": p} for o, p in post.items()], origin_confidence=band, margin=margin, channels_observed=channels_observed(ev),
               suggested_actions=suggested_actions(inv["mode"], top), next_actions=ranked, unavailable_actions=unavailable, recommendation=rec, stop_reason=reason, calibration="uncalibrated")


async def _attempt_evidence(gh: GitHub, owner: str, name: str, run: dict, attempt: int, mine: set[str]) -> list[Evidence]:
    latest = int(run.get("run_attempt") or 1)
    others = []
    for k in range(1, latest + 1):
        if k == attempt or len(others) >= 3:
            continue
        jobs = await gh.jobs(owner, name, run["id"], k)
        if not jobs:
            continue
        if all(j.get("conclusion") == "success" for j in jobs):
            others.append({"attempt": k, "conclusion": "success"})
        else:
            others.append({"attempt": k, "conclusion": "failure", "same_signature": any(level1(j) in mine for j in _failed_jobs(jobs))})
    return attempts_evidence(others)


async def start(gh: GitHub, state: State, repo: str, *, run_id=None, job_id=None, pr_number=None, branch=None, sha=None, run_attempt: int | None = None,
                max_jobs: int = 8, max_excerpt_chars: int = 6000, progress: Progress | None = None) -> dict:
    owner, name = parse_repo(repo)
    if sum(1 for x in (run_id, job_id, pr_number, branch, sha) if x) != 1:
        raise GitHubError("exactly one of run_id, job_id, pr_number, branch, sha is required")
    res = await resolve_run(gh, owner, name, run_id=run_id, job_id=job_id, pr_number=pr_number, branch=branch, sha=sha)
    if "run" not in res:
        return res
    run = res["run"]
    attempt = int(run_attempt or run.get("run_attempt") or 1)
    inv_id = f"{owner}/{name}#{run['id']}@{attempt}"
    existing = state.load(inv_id)
    if existing:
        return get(state, inv_id)
    jobs = await gh.jobs(owner, name, run["id"], attempt if run_attempt else None)
    run_view = {"id": run["id"], "url": run.get("html_url"), "workflow_path": run.get("path"), "workflow_id": run.get("workflow_id"), "head_sha": run.get("head_sha"), "head_branch": run.get("head_branch"),
                "attempt": attempt, "event": run.get("event"), "pull_requests": [p.get("number") for p in run.get("pull_requests") or []], "conclusion": run.get("conclusion")}
    inv: dict[str, Any] = {"investigation_id": inv_id, "repo": f"{owner}/{name}", "run": run_view, "taken": [], "has_pr": bool(run_view["pull_requests"]) or run.get("event") == "pull_request",
                           "job_count": len(jobs), "attempt_count": int(run.get("run_attempt") or 1), "evidence_added": []}
    failed = _failed_jobs(jobs)
    if not jobs and run.get("conclusion") == "startup_failure":
        inv.update(clusters=[], jobs_not_downloaded=[], mode="config", mode_confidence="high", mode_runner_up=None, mode_margin=1.0, slice={"window": "whole_log", "mismatch": False},
                   summary=f"Workflow failed to start: {run.get('path')} is invalid or references something that does not exist", root_cause_hint="Fix the workflow YAML; GitHub rejected it before any job ran.")
    elif not failed:
        inv.update(clusters=[], jobs_not_downloaded=[], mode="unknown", mode_confidence="low", mode_runner_up=None, mode_margin=1.0, slice={"window": "whole_log", "mismatch": False}, summary="Run failed but no job has a failed conclusion", root_cause_hint="")
    else:
        chosen, rest = prioritize(failed, max_jobs)
        await download(gh, state, owner, name, chosen, progress)
        analysed = [(j, analyze(state.cached_log(j["id"]) or "", j, max_excerpt_chars=max_excerpt_chars)) for j in chosen]  # one log in memory at a time
        clusters = cluster(analysed)
        best = clusters[0]
        a = next(x for j, x in analysed if j["id"] == best["representative_job"]["job_id"])
        inv.update(clusters=clusters, jobs_not_downloaded=[{"job_id": j["id"], "name": j["name"]} for j in rest], mode=a.mode, mode_confidence=a.mode_confidence, mode_runner_up=a.mode_runner_up,
                   mode_margin=a.mode_margin, summary=a.summary, root_cause_hint=a.root_cause_hint, slice={"window": a.slice_window, "mismatch": a.slice_mismatch},
                   referenced_files=sorted({f["path"] for c in clusters for f in c["representative_job"]["files"]}),
                   failing_test_files=sorted({t["file"] for c in clusters for t in c["representative_job"]["failed_tests"] if t.get("file")}))
        inv["level1_signatures"] = sorted({level1(j) for j in failed})
        # cost-0 channels. Matrix evidence only compares a failed job with its own family (same name prefix):
        # a lone "typing" job failing next to twelve unrelated green jobs says nothing about flakiness.
        families = {_prefix(f["name"]) for f in failed}
        family_jobs = [j for j in jobs if _prefix(j["name"]) in families]
        total, nfail = len(family_jobs), len(failed)
        axis = any(j.get("conclusion") == "success" for j in family_jobs)
        for e in matrix_evidence(total, nfail, len(clusters) == 1, axis):
            state.add_evidence(inv_id, e.channel, e.observation, e.weights)
            inv["evidence_added"].append(asdict(e))
        if inv["attempt_count"] > 1:
            for e in await _attempt_evidence(gh, owner, name, run, attempt, set(inv["level1_signatures"])):
                state.add_evidence(inv_id, e.channel, e.observation, e.weights)
                inv["evidence_added"].append(asdict(e))
        inv["taken"] += ["fetch_sibling_jobs"] + (["fetch_attempts"] if inv["attempt_count"] > 1 else [])
    inv.setdefault("referenced_files", [])
    inv.setdefault("failing_test_files", [])
    inv.setdefault("level1_signatures", [])
    _belief(state, inv)
    state.save(inv_id, inv["repo"], run["id"], attempt, inv)
    return inv


# ---------------------------------------------------------------------------
# gather / outcome
# ---------------------------------------------------------------------------

def get(state: State, inv_id: str) -> dict:
    inv = state.load(inv_id)
    if not inv:
        raise NotFound(f"unknown investigation {inv_id}; call start_investigation for that run")
    inv["evidence_trail"] = state.evidence(inv_id)
    inv["linked_investigations"] = [s for s in state.siblings(inv["repo"], inv["run"]["id"]) if s != inv_id]
    return inv


async def gather(gh: GitHub, state: State, inv_id: str, action: str, job_id: int | None = None) -> dict:
    inv = state.load(inv_id)
    if not inv:
        raise NotFound(f"unknown investigation {inv_id}; call start_investigation for that run")
    if action not in act.ACTIONS:
        raise GitHubError(f"unknown action {action!r}", hint=f"one of {', '.join(act.ACTIONS)}")
    owner, name = inv["repo"].split("/")
    run = inv["run"]
    before = {o["origin"]: o["probability"] for o in inv["origin"]}
    added: list[Evidence] = []
    payload: dict[str, Any] = {}
    if action == "fetch_sibling_jobs":
        jobs = await gh.jobs(owner, name, run["id"], run["attempt"] if run["attempt"] != inv["attempt_count"] else None)
        payload["sibling_jobs"] = [{"job_id": j["id"], "name": j["name"], "conclusion": j.get("conclusion"), "level1_signature": level1(j)} for j in jobs]
    elif action == "fetch_attempts":
        full = await gh.run(owner, name, run["id"])
        added += await _attempt_evidence(gh, owner, name, full, run["attempt"], set(inv["level1_signatures"]))
        payload["attempts"] = [asdict(e) for e in added]
    elif action == "fetch_annotations":
        ann = []
        for c in inv["clusters"]:
            ann += await gh.annotations(owner, name, c["representative_job"]["job_id"])
        payload["annotations"] = [{"path": a.get("path"), "line": a.get("start_line"), "level": a.get("annotation_level"), "message": a.get("message")} for a in ann][:50]
        state.mark_channel(inv_id, "annotations")
        inv["referenced_files"] = sorted(set(inv["referenced_files"]) | {a.get("path") for a in ann if a.get("path")})
    elif action in ("fetch_pr_diff", "fetch_pr_history"):
        pr = run["pull_requests"][0] if run["pull_requests"] else None
        if pr is None:
            raise GitHubError("run has no pull request", hint="use fetch_base_history instead")
        if action == "fetch_pr_diff":
            files = await gh.pull_files(owner, name, pr)
            changed = [f["filename"] for f in files]
            payload["changed_files"] = [{"path": f["filename"], "status": f.get("status"), "changes": f.get("changes")} for f in files]
            added += diff_evidence(inv["mode"], changed, inv["referenced_files"], inv["failing_test_files"])
            inv["workflow_change_observed"] = any(f.startswith(".github/workflows/") for f in changed)
        else:
            pull = await gh.pull(owner, name, pr)
            runs = await gh.runs(owner, name, 30, event="pull_request", branch=pull["head"]["ref"])
            earlier, seen = [], set()
            for r in sorted(runs, key=lambda r: r.get("created_at") or "", reverse=True):
                if r.get("workflow_id") != run["workflow_id"] or r["head_sha"] in seen or r["head_sha"] == run["head_sha"] or r.get("status") != "completed":
                    continue
                seen.add(r["head_sha"])
                same = False
                if r.get("conclusion") != "success" and len(earlier) < 3:
                    same = any(level1(j) in inv["level1_signatures"] for j in _failed_jobs(await gh.jobs(owner, name, r["id"])))
                earlier.append({"sha": r["head_sha"], "conclusion": r.get("conclusion"), "same_signature": same})
                if len(earlier) >= 5:
                    break
            payload["pr_runs"] = earlier
            added += pr_history_evidence(earlier)
    elif action == "fetch_base_history":
        base = (await gh.pull(owner, name, run["pull_requests"][0]))["base"]["ref"] if run["pull_requests"] else run["head_branch"]
        runs = await gh.workflow_runs(owner, name, run["workflow_id"], 20, branch=base, status="completed")
        runs = [r for r in runs if r["id"] != run["id"]]
        latest = runs[0] if runs else None
        failed_runs = [r for r in runs if r.get("conclusion") in FAILED_RUN]
        same = False
        if failed_runs:
            same = any(level1(j) in inv["level1_signatures"] for j in _failed_jobs(await gh.jobs(owner, name, failed_runs[0]["id"])))
        payload["base_runs"] = [_brief(r) for r in runs[:10]]
        added += base_history_evidence(same and failed_runs and failed_runs[0] is latest, bool(latest and latest.get("conclusion") == "success"), len(failed_runs), len(runs))
    elif action == "fetch_full_step_log":
        jid = job_id or inv["clusters"][0]["representative_job"]["job_id"]
        text = state.cached_log(jid) or await gh.job_log(owner, name, jid)
        state.cache_log(jid, text)
        job = await gh.job(owner, name, jid)
        a = analyze(text, job, max_excerpt_chars=EXCERPT_BUDGET)
        payload["log_excerpt"] = a.log_excerpt if a.slice_window != "whole_log" else excerpt(parse_lines(text), max(0, len(parse_lines(text)) - 200), max_chars=EXCERPT_BUDGET, before=0, after=400)
        if inv["mode"] == "unknown" and a.mode != "unknown":
            inv.update(mode=a.mode, mode_confidence=a.mode_confidence, mode_runner_up=a.mode_runner_up, mode_margin=a.mode_margin, summary=a.summary, root_cause_hint=a.root_cause_hint)
    for e in added:
        state.add_evidence(inv_id, e.channel, e.observation, e.weights)
    if action not in inv["taken"]:
        inv["taken"].append(action)
    _belief(state, inv)
    state.save(inv_id, inv["repo"], run["id"], run["attempt"], inv)
    after = {o["origin"]: o["probability"] for o in inv["origin"]}
    return {"investigation_id": inv_id, "action": action, "evidence_added": [asdict(e) for e in added], "origin_before": before, "origin_after": after, "mode": inv["mode"], "mode_confidence": inv["mode_confidence"],
            "channels_observed": inv["channels_observed"], "next_actions": inv["next_actions"], "unavailable_actions": inv["unavailable_actions"], "recommendation": inv["recommendation"], "stop_reason": inv["stop_reason"],
            "suggested_actions": inv["suggested_actions"], **payload}


def record_outcome(state: State, inv_id: str, actual_mode: str, actual_origin: str, fix_summary: str, fix_commit: str | None = None, fix_files: list[str] | None = None, resolved_by: str | None = None) -> dict:
    inv = state.load(inv_id)
    if not inv:
        raise NotFound(f"unknown investigation {inv_id}; call start_investigation for that run")
    derived = derive_origin(resolved_by, fix_files or [], bool(inv.get("workflow_change_observed")))
    oid = state.record_outcome(inv_id, actual_mode, actual_origin, derived, resolved_by, fix_summary, fix_commit, fix_files or [])
    return {"investigation_id": inv_id, "outcome_id": oid, "actual_mode": actual_mode, "actual_origin": actual_origin, "derived_origin": derived, "calibration": "uncalibrated", "outcomes_recorded": state.outcome_count()}
