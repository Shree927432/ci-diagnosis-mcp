"""Investigation lifecycle: resolve target, download and analyse failed jobs, cluster, gather evidence, record outcomes.

Three sections (resolve/download, cluster/channels, gather/outcome) in one module; the server layer only calls
start(), gather(), get() and record_outcome().
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from typing import Any, Awaitable, Callable

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
MAX_JOBS = 15  # keeps NFR-4 (40 API calls) with attempts and the run/jobs calls
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
    """Returns {'run': run, 'pr': pull|None} or {'candidate_runs': [...]} or {'error': msg, 'latest_runs': [...]}."""
    pr = await gh.pull(owner, name, int(pr_number)) if pr_number else None
    if job_id:
        run_id = (await gh.job(owner, name, int(job_id)))["run_id"]
    if run_id:
        run = await gh.run(owner, name, int(run_id))
        if run.get("status") != "completed":
            return {"error": f"run {run_id} is still {run.get('status')}; wait for it to complete", "latest_runs": [_brief(run)]}
        return {"run": run, "pr": pr}
    if pr:
        sha = pr["head"]["sha"]
    runs = await gh.runs(owner, name, 50, **({"head_sha": sha} if sha else {"branch": branch}))
    if branch and runs:  # runs are newest first: only the branch head's commit is of interest
        head = runs[0]["head_sha"]
        runs = [r for r in runs if r["head_sha"] == head]
    failed = _failed_runs(runs)
    if len(failed) == 1:
        return {"run": failed[0], "pr": pr}
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


async def download(gh: GitHub, state: State, owner: str, name: str, jobs: list[dict], progress: Progress | None) -> dict[int, str]:
    """Download up to 4 logs concurrently straight into the disk cache (NFR-3). Returns {job_id: reason} for failures."""
    sem, done, failures = asyncio.Semaphore(4), 0, {}

    async def one(j: dict) -> None:
        nonlocal done
        async with sem:
            if state.cached_log(j["id"]) is None:
                state.cache_log(j["id"], await gh.job_log(owner, name, j["id"]))
        done += 1
        if progress:
            await progress(done, len(jobs), f"downloaded log for {j['name']}")

    results = await asyncio.gather(*(one(j) for j in jobs), return_exceptions=True)
    for j, r in zip(jobs, results):
        if isinstance(r, BaseException):
            failures[j["id"]] = str(r) if isinstance(r, GitHubError) else f"download failed: {type(r).__name__}"
    return failures


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
            "errors_omitted": a.errors_total - len(a.errors), "tests_omitted": a.tests_total - len(a.failed_tests), "files_omitted": a.files_total - len(a.files), "level1_signature": level1(job)}


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
    """Compare the three attempts nearest to the one under investigation (FR-9)."""
    latest = int(run.get("run_attempt") or 1)
    nearest = sorted((k for k in range(1, latest + 1) if k != attempt), key=lambda k: abs(k - attempt))[:3]
    others = []
    for k in nearest:
        jobs = await gh.jobs(owner, name, run["id"], k)
        if not jobs or any(j.get("status") not in (None, "completed") for j in jobs):
            continue  # still running: no evidence
        failed = _failed_jobs(jobs)
        if not failed:
            others.append({"attempt": k, "conclusion": "success"})
        else:
            others.append({"attempt": k, "conclusion": "failure", "same_signature": any(level1(j) in mine for j in failed)})
    return attempts_evidence(others)


def _add(state: State, inv: dict, evidence: list[Evidence]) -> None:
    for e in evidence:
        state.add_evidence(inv["investigation_id"], e.channel, e.observation, e.weights)
        inv["evidence_added"].append(asdict(e))


async def start(gh: GitHub, state: State, repo: str, *, run_id=None, job_id=None, pr_number=None, branch=None, sha=None, run_attempt: int | None = None,
                max_jobs: int = 8, max_excerpt_chars: int = 6000, progress: Progress | None = None) -> dict:
    owner, name = parse_repo(repo)
    if sum(1 for x in (run_id, job_id, pr_number, branch, sha) if x) != 1:
        raise GitHubError("exactly one of run_id, job_id, pr_number, branch, sha is required")
    res = await resolve_run(gh, owner, name, run_id=run_id, job_id=job_id, pr_number=pr_number, branch=branch, sha=sha)
    if "run" not in res:
        return res
    run, pr = res["run"], res.get("pr")
    attempt = int(run_attempt or run.get("run_attempt") or 1)
    inv_id = f"{owner}/{name}#{run['id']}@{attempt}"
    if state.load(inv_id):
        return get(state, inv_id)
    jobs = await gh.jobs(owner, name, run["id"], attempt if run_attempt else None)
    pr_numbers = [p.get("number") for p in run.get("pull_requests") or []] or ([pr["number"]] if pr else [])
    run_view = {"id": run["id"], "url": run.get("html_url"), "workflow_path": run.get("path"), "workflow_id": run.get("workflow_id"), "head_sha": run.get("head_sha"), "head_branch": run.get("head_branch"),
                "attempt": attempt, "event": run.get("event"), "pull_requests": pr_numbers, "conclusion": run.get("conclusion"), "created_at": run.get("created_at")}
    inv: dict[str, Any] = {"investigation_id": inv_id, "repo": f"{owner}/{name}", "run": run_view, "taken": [], "has_pr": bool(pr_numbers), "pr_base_ref": pr["base"]["ref"] if pr else None,
                           "job_count": len(jobs), "attempt_count": int(run.get("run_attempt") or 1), "evidence_added": [], "referenced_files": [], "failing_test_files": [], "level1_signatures": []}
    failed = _failed_jobs(jobs)
    if not jobs and run.get("conclusion") == "startup_failure":
        inv.update(clusters=[], jobs_not_downloaded=[], mode="config", mode_confidence="high", mode_runner_up=None, mode_margin=1.0, slice={"window": "whole_log", "mismatch": False},
                   summary=f"Workflow failed to start: {run.get('path')} is invalid or references something that does not exist", root_cause_hint="Fix the workflow YAML; GitHub rejected it before any job ran.")
    elif not failed:
        inv.update(clusters=[], jobs_not_downloaded=[], mode="unknown", mode_confidence="low", mode_runner_up=None, mode_margin=1.0, slice={"window": "whole_log", "mismatch": False},
                   summary="Run failed but no job has a failed conclusion", root_cause_hint="")
    else:
        chosen, rest = prioritize(failed, max(1, min(max_jobs, MAX_JOBS)))
        failures = await download(gh, state, owner, name, chosen, progress)
        if len(failures) == len(chosen):
            raise GitHubError("could not download any failed job log: " + "; ".join(failures.values()), hint="logs may have expired (90 days) or the token lacks actions:read")
        analysed = [(j, analyze(state.cached_log(j["id"]) or "", j, max_excerpt_chars=max_excerpt_chars)) for j in chosen if j["id"] not in failures]  # one log in memory at a time
        clusters = cluster(analysed)
        best = clusters[0]
        a = next(x for j, x in analysed if j["id"] == best["representative_job"]["job_id"])
        inv.update(clusters=clusters, mode=a.mode, mode_confidence=a.mode_confidence, mode_runner_up=a.mode_runner_up, mode_margin=a.mode_margin, summary=a.summary, root_cause_hint=a.root_cause_hint,
                   slice={"window": a.slice_window, "mismatch": a.slice_mismatch},
                   jobs_not_downloaded=[{"job_id": j["id"], "name": j["name"], "reason": "max_jobs"} for j in rest] + [{"job_id": j["id"], "name": j["name"], "reason": failures[j["id"]]} for j in chosen if j["id"] in failures],
                   referenced_files=sorted({f["path"] for c in clusters for f in c["representative_job"]["files"]}),
                   failing_test_files=sorted({t["file"] for c in clusters for t in c["representative_job"]["failed_tests"] if t.get("file")}),
                   level1_signatures=sorted({level1(j) for j in failed}))
        # cost-0 channels. Matrix evidence only compares a failed job with its own family (same name prefix):
        # a lone "typing" job failing next to twelve unrelated green jobs says nothing about flakiness.
        families = {_prefix(f["name"]) for f in failed}
        family_jobs = [j for j in jobs if _prefix(j["name"]) in families]
        _add(state, inv, matrix_evidence(len(family_jobs), len(failed), len(clusters) == 1, any(j.get("conclusion") == "success" for j in family_jobs)))
        if len(jobs) > 1:
            inv["taken"].append("fetch_sibling_jobs")
        if inv["attempt_count"] > 1:
            _add(state, inv, await _attempt_evidence(gh, owner, name, run, attempt, set(inv["level1_signatures"])))
            inv["taken"].append("fetch_attempts")
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


async def _base_ref(gh: GitHub, owner: str, name: str, inv: dict) -> str:
    if inv.get("pr_base_ref"):
        return inv["pr_base_ref"]
    if inv["run"]["pull_requests"]:
        inv["pr_base_ref"] = (await gh.pull(owner, name, inv["run"]["pull_requests"][0]))["base"]["ref"]
        return inv["pr_base_ref"]
    return inv["run"]["head_branch"]


async def gather(gh: GitHub, state: State, inv_id: str, action: str, job_id: int | None = None) -> dict:
    inv = state.load(inv_id)
    if not inv:
        raise NotFound(f"unknown investigation {inv_id}; call start_investigation for that run")
    if action not in act.ACTIONS:
        raise GitHubError(f"unknown action {action!r}", hint=f"one of {', '.join(act.ACTIONS)}")
    owner, name = inv["repo"].split("/")
    run = inv["run"]
    created = run.get("created_at") or ""
    before = {o["origin"]: o["probability"] for o in inv["origin"]}
    inv["evidence_added"] = []
    payload: dict[str, Any] = {}
    if action == "fetch_sibling_jobs":
        jobs = await gh.jobs(owner, name, run["id"], run["attempt"] if run["attempt"] != inv["attempt_count"] else None)
        payload["sibling_jobs"] = [{"job_id": j["id"], "name": j["name"], "conclusion": j.get("conclusion"), "level1_signature": level1(j)} for j in jobs]
    elif action == "fetch_attempts":
        full = await gh.run(owner, name, run["id"])
        _add(state, inv, await _attempt_evidence(gh, owner, name, full, run["attempt"], set(inv["level1_signatures"])))
        payload["attempts"] = list(inv["evidence_added"])
    elif action == "fetch_annotations":
        ann = []
        for c in inv["clusters"]:
            ann += await gh.annotations(owner, name, c["representative_job"]["job_id"])
        payload["annotations"] = [{"path": a.get("path"), "line": a.get("start_line"), "level": a.get("annotation_level"), "message": a.get("message")} for a in ann][:50]
        state.mark_channel(inv_id, "annotations")
        inv["referenced_files"] = sorted(set(inv["referenced_files"]) | {a.get("path") for a in ann if a.get("path")})
    elif action in ("fetch_pr_diff", "fetch_pr_history"):
        if not run["pull_requests"]:
            raise GitHubError("run has no pull request", hint="use fetch_base_history instead")
        pr = run["pull_requests"][0]
        if action == "fetch_pr_diff":
            files = await gh.pull_files(owner, name, pr)
            changed = [f["filename"] for f in files]
            payload["changed_files"] = [{"path": f["filename"], "status": f.get("status"), "changes": f.get("changes")} for f in files]
            _add(state, inv, diff_evidence(inv["mode"], changed, inv["referenced_files"], inv["failing_test_files"]))
            inv["workflow_change_observed"] = any(f.startswith(".github/workflows/") for f in changed)
        else:
            pull = await gh.pull(owner, name, pr)
            inv["pr_base_ref"] = pull["base"]["ref"]
            runs = await gh.runs(owner, name, 30, event="pull_request", branch=pull["head"]["ref"])
            earlier, seen = [], set()
            for r in sorted(runs, key=lambda r: r.get("created_at") or "", reverse=True):
                if r.get("workflow_id") != run["workflow_id"] or r["head_sha"] in seen or r["head_sha"] == run["head_sha"] or r.get("status") != "completed" or (r.get("created_at") or "") >= created:
                    continue
                seen.add(r["head_sha"])
                same = r.get("conclusion") != "success" and any(level1(j) in inv["level1_signatures"] for j in _failed_jobs(await gh.jobs(owner, name, r["id"])))
                earlier.append({"sha": r["head_sha"], "conclusion": r.get("conclusion"), "same_signature": same})
                if len(earlier) >= 3:  # every recorded push is signature-checked; three bounds the API calls
                    break
            payload["pr_runs"] = earlier
            _add(state, inv, pr_history_evidence(earlier))
    elif action == "fetch_base_history":
        base = await _base_ref(gh, owner, name, inv)
        runs = await gh.workflow_runs(owner, name, run["workflow_id"], 20, branch=base, status="completed")
        runs = [r for r in runs if r["id"] != run["id"] and r.get("event") == "push" and (r.get("created_at") or "") < created]
        latest = runs[0] if runs else None
        failed_runs = [r for r in runs if r.get("conclusion") in FAILED_RUN]
        matches = []
        for r in failed_runs[:3]:  # level-1 signature check bounded to three extra calls
            matches.append(any(level1(j) in inv["level1_signatures"] for j in _failed_jobs(await gh.jobs(owner, name, r["id"]))))
        payload["base_runs"] = [_brief(r) for r in runs[:10]]
        _add(state, inv, base_history_evidence(bool(matches) and matches[0] and failed_runs[0] is latest, bool(latest and latest.get("conclusion") == "success"), sum(matches), len(runs)))
    elif action == "fetch_full_step_log":
        if not job_id and not inv["clusters"]:
            raise GitHubError("this investigation has no failed job logs", hint="pass job_id for the job whose log you want")
        jid = job_id or inv["clusters"][0]["representative_job"]["job_id"]
        text = state.cached_log(jid)
        if text is None:
            text = await gh.job_log(owner, name, jid)
            state.cache_log(jid, text)
        a = analyze(text, await gh.job(owner, name, jid), max_excerpt_chars=EXCERPT_BUDGET)
        payload["log_excerpt"] = a.log_excerpt
        if inv["mode"] == "unknown" and a.mode != "unknown":
            inv.update(mode=a.mode, mode_confidence=a.mode_confidence, mode_runner_up=a.mode_runner_up, mode_margin=a.mode_margin, summary=a.summary, root_cause_hint=a.root_cause_hint)
    if action not in inv["taken"]:
        inv["taken"].append(action)
    _belief(state, inv)
    state.save(inv_id, inv["repo"], run["id"], run["attempt"], inv)
    after = {o["origin"]: o["probability"] for o in inv["origin"]}
    return {"investigation_id": inv_id, "action": action, "evidence_added": inv["evidence_added"], "origin_before": before, "origin_after": after, "mode": inv["mode"], "mode_confidence": inv["mode_confidence"],
            "channels_observed": inv["channels_observed"], "next_actions": inv["next_actions"], "unavailable_actions": inv["unavailable_actions"], "recommendation": inv["recommendation"], "stop_reason": inv["stop_reason"],
            "suggested_actions": inv["suggested_actions"], **payload}


def record_outcome(state: State, inv_id: str, actual_mode: str, actual_origin: str, fix_summary: str, fix_commit: str | None = None, fix_files: list[str] | None = None, resolved_by: str | None = None) -> dict:
    inv = state.load(inv_id)
    if not inv:
        raise NotFound(f"unknown investigation {inv_id}; call start_investigation for that run")
    derived = derive_origin(resolved_by, fix_files or [], bool(inv.get("workflow_change_observed")))
    oid = state.record_outcome(inv_id, actual_mode, actual_origin, derived, resolved_by, fix_summary, fix_commit, fix_files or [])
    return {"investigation_id": inv_id, "outcome_id": oid, "actual_mode": actual_mode, "actual_origin": actual_origin, "derived_origin": derived, "calibration": "uncalibrated", "outcomes_recorded": state.outcome_count()}
