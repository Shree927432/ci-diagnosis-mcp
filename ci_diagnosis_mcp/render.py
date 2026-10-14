"""Markdown renderings of tool results for the agent to read (structured_content carries the same data)."""
from __future__ import annotations


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def _errors(errs: list[dict], limit: int = 12) -> list[str]:
    out = []
    for e in errs[:limit]:
        loc = f"{e['file']}:{e['line']}" if e.get("file") and e.get("line") else (e.get("file") or "")
        code = f" [{e['code']}]" if e.get("code") else (f" [{e['rule']}]" if e.get("rule") else "")
        out.append(f"- {loc + ': ' if loc else ''}{e['message']}{code} (L{e['log_line']})")
    if len(errs) > limit:
        out.append(f"- … {len(errs) - limit} more")
    return out


def _job(j: dict) -> list[str]:
    lines = [f"**Job** {j['name']} (id {j['job_id']}) · step `{j.get('failed_step') or '?'}` · command `{j.get('failing_command') or '?'}` · exit {j.get('exit_code')}",
             f"slice: {j['slice']['window']}{' (mismatch: whole-log mode differed)' if j['slice'].get('mismatch') else ''}"]
    if j.get("errors"):
        lines += ["", "Errors:"] + _errors(j["errors"])
    if j.get("failed_tests"):
        lines += ["", "Failed tests:"] + [f"- {t['name']}" + (f" ({t['file']})" if t.get("file") else "") + (f": {t['message']}" if t.get("message") else "") for t in j["failed_tests"][:15]]
    if j.get("files"):
        lines += ["", "Files: " + ", ".join(f"{f['path']}" + (f":{f['line']}" if f.get("line") else "") for f in j["files"][:10])]
    lines += ["", j["log_excerpt"]]
    return lines


def _belief(inv: dict) -> list[str]:
    top = inv["origin"][0]
    lines = [f"**Mode**: `{inv['mode']}` ({inv['mode_confidence']} confidence" + (f", runner-up `{inv['mode_runner_up']}`" if inv.get("mode_runner_up") else "") + ")",
             f"**Origin**: " + ", ".join(f"{o['origin']} {_pct(o['probability'])}" for o in inv["origin"]) + f" ({inv['origin_confidence']} confidence; channels: {', '.join(inv['channels_observed']) or 'none, prior only'}; {inv['calibration']})",
             f"**Suggested fix** ({inv['mode']} / {top['origin']}):"] + [f"- {s}" for s in inv["suggested_actions"]]
    lines.append(f"**Recommendation**: {inv['recommendation']} ({inv['stop_reason']})")
    if inv["next_actions"]:
        lines.append("Next actions: " + ", ".join(f"`{a['action']}` (score {a['score']}, cost {a['cost']})" for a in inv["next_actions"]))
    if inv["unavailable_actions"]:
        lines.append("Unavailable: " + "; ".join(f"{a['action']}: {a['reason']}" for a in inv["unavailable_actions"]))
    return lines


def start(inv: dict) -> str:
    run = inv["run"]
    lines = [f"# CI diagnosis {inv['investigation_id']}", f"{run.get('workflow_path')} · {run.get('event')} · {run.get('head_branch')} @ {str(run.get('head_sha'))[:10]} · attempt {run['attempt']} · {run.get('url')}", "",
             f"**{inv['summary']}**", inv.get("root_cause_hint") or "", ""] + _belief(inv)
    for c in inv["clusters"]:
        lines += ["", f"## Cluster `{c['signature']}` ({c['job_count']} job{'s' if c['job_count'] != 1 else ''})"] + _job(c["representative_job"])
        if c["other_jobs"]:
            lines.append("Same signature: " + ", ".join(f"{j['name']} ({j['job_id']})" for j in c["other_jobs"][:10]))
    if inv["jobs_not_downloaded"]:
        lines += ["", "Not downloaded (max_jobs): " + ", ".join(f"{j['name']} ({j['job_id']})" for j in inv["jobs_not_downloaded"])]
    return "\n".join(lines)


def gather(g: dict) -> str:
    lines = [f"# Evidence: {g['action']} on {g['investigation_id']}"]
    lines += [f"- {e['channel']}: {e['observation']} → " + ", ".join(f"{k} {v}" for k, v in e["weights"].items()) for e in g["evidence_added"]] or ["- no new evidence"]
    lines.append("Origin: " + ", ".join(f"{k} {_pct(g['origin_before'][k])}→{_pct(v)}" for k, v in g["origin_after"].items()))
    lines.append(f"Recommendation: {g['recommendation']} ({g['stop_reason']})" + (" · next: " + ", ".join(a["action"] for a in g["next_actions"]) if g["next_actions"] else ""))
    for key in ("changed_files", "sibling_jobs", "attempts", "pr_runs", "base_runs", "annotations"):
        if g.get(key):
            lines += ["", f"{key}:"] + [f"- {x}" for x in g[key][:20]]
    if g.get("log_excerpt"):
        lines += ["", g["log_excerpt"]]
    return "\n".join(lines)


def analyze(a: dict) -> str:
    lines = [f"**{a['summary']}**", a.get("root_cause_hint") or "", f"Mode: `{a['mode']}` ({a['mode_confidence']}) · parser {a.get('parser')} · step `{a.get('failed_step') or '?'}` · exit {a.get('exit_code')} · slice {a['slice']['window']}",
             f"Suggested (assuming origin {a['assumed_origin']}):"] + [f"- {s}" for s in a["suggested_actions"]]
    if a["errors"]:
        lines += ["", "Errors:"] + _errors(a["errors"])
    if a["failed_tests"]:
        lines += ["", "Failed tests:"] + [f"- {t['name']}" + (f" ({t['file']})" if t.get("file") else "") for t in a["failed_tests"][:15]]
    if a["files"]:
        lines += ["", "Files: " + ", ".join(f['path'] + (f":{f['line']}" if f.get("line") else "") for f in a["files"][:10])]
    lines += ["", a["log_excerpt"]]
    return "\n".join(lines)
