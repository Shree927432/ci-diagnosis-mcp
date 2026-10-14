"""GitHub Actions runner, infrastructure, resource and configuration messages."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, find_all, find_first, find_last, obs, trunc

NAME = "gha"
GATE = re.compile(r"""lost communication|shutdown signal|maximum execution time|has timed out after|was canceled|operation was canceled|Step canceled by GitHub|heap out of memory|FATAL ERROR|OOMKilled|Out of memory|Cannot allocate memory|OutOfMemoryError|MemoryError|exit code 137|^Killed$|No space left|ENOSPC|toomanyrequests|rate limit|Too Many Requests|Unable to resolve action|Input required|Resource not accessible|could not read Username|Authentication failed|Repository not found|Unable to locate executable|command not found|: not found$|lock file is not found|not found for platform|Unable to find (?:Node|Python|Go|Java)|template is not valid|Unrecognized named-value|ENOTFOUND|EAI_AGAIN|ECONNRESET|ETIMEDOUT|ECONNREFUSED|Could not resolve host|name resolution|TLS handshake|Connection (?:timed out|reset)|Unexpected input\(s\)|" length must be|no matching workflow run found|EBUSY|ECANCELED""", re.M)

# (pattern, mode, weight, summary, hint). Summary may use {0} for group 1.
RULES = [
    (r"lost communication with the server", "infra", "decisive", "Runner lost communication with GitHub", "The runner VM died or lost network; not caused by the code. Re-run the job."),
    (r"The runner has received a shutdown signal", "infra", "decisive", "Runner received a shutdown signal", "Runner was pre-empted; re-run."),
    (r"has exceeded the maximum execution time of (\d+) minutes", "timeout", "decisive", "Job exceeded its {0}-minute timeout", "Find the step that hung (last output before cancellation) or raise timeout-minutes if the work legitimately grew."),
    (r"The action '([^']+)' has timed out after (\d+) minutes", "timeout", "decisive", "Step '{0}' timed out", "A step hit its timeout-minutes; look at the last output before the timeout."),
    (r"The job was canceled because \"([^\"]+)\" failed", "cancelled", "decisive", "Cancelled because sibling job \"{0}\" failed (fail-fast)", "This job did not fail on its own; diagnose the sibling job instead."),
    (r"^(?:##\[error\])?The operation was canceled\.?$", "cancelled", "medium", "Job was cancelled", "Cancelled by a newer push, a manual cancel, a sibling failure, or a timeout; check the run page."),
    (r"JavaScript heap out of memory|FATAL ERROR: (?:Reached heap limit|Ineffective mark-compacts)", "out_of_memory", "decisive", "Node.js ran out of heap memory", "Raise NODE_OPTIONS=--max-old-space-size, shard the work, or find the memory regression."),
    (r"\bOOMKilled\b|Out of memory: Killed process|Cannot allocate memory|java\.lang\.OutOfMemoryError|^MemoryError", "out_of_memory", "decisive", "Process was killed for running out of memory", "Reduce parallelism, raise limits, or use a larger runner."),
    (r"Process completed with exit code 137\b|^Killed$", "out_of_memory", "strong", "Process killed with SIGKILL (exit code 137), usually out-of-memory", "Exit 137 = SIGKILL, most often the OOM killer; reduce memory use or workers."),
    (r"No space left on device|\bENOSPC\b", "infra", "decisive", "Runner ran out of disk space", "Free disk before the heavy step or reduce artifact size."),
    (r"toomanyrequests: You have reached your pull rate limit", "infra", "decisive", "Docker Hub pull rate limit reached", "Authenticate to Docker Hub or mirror the base image to GHCR."),
    (r"API rate limit exceeded|429 Too Many Requests|secondary rate limit", "infra", "strong", "Hit an API rate limit", "Authenticate requests or add backoff; re-running later usually succeeds."),
    (r"Unable to resolve action `?([^`\s,]+)`?", "config", "decisive", "Workflow references an action that cannot be resolved: {0}", "Typo in uses:, a tag that does not exist, or a private action without access."),
    (r"Input required and not supplied: (\w+)", "config", "decisive", "Required action input \"{0}\" was not supplied", "Add the missing with: input to the step."),
    (r"Resource not accessible by integration", "config", "decisive", "GITHUB_TOKEN lacks permission for an API call", "Add the needed permissions: block (e.g. pull-requests: write), or the event is from a fork with a read-only token."),
    (r"could not read Username for 'https://github\.com'|Authentication failed for 'https://github\.com|remote: Repository not found\.", "config", "decisive", "Git could not authenticate to GitHub", "A checkout or private dependency needs a token; pass token: to actions/checkout or use a PAT/App."),
    (r"Unable to locate executable file: (\S+)", "config", "decisive", "Executable not found on runner: {0}", "Install the tool in an earlier step or use the matching setup-* action."),
    (r"(?:^|: )(\S+): (?:command not found|not found)$", "config", "decisive", "Command not found: {0}", "The binary is not installed or not on PATH in CI; install it or run it via the package manager."),
    (r"Dependencies lock file is not found in", "config", "decisive", "setup-* cache requires a lockfile that is not in the repo", "Commit the lockfile or remove the cache: option."),
    (r"Version (\S+) with arch \S+ not found for platform|Unable to find (?:Node|Python|Go|Java) version '?([^'\s]+)'?", "config", "decisive", "Requested toolchain version is not available", "Pick a version that exists for the runner platform."),
    (r"The template is not valid|Unrecognized named-value", "config", "decisive", "Workflow YAML is invalid", "Fix the expression or key reported in the workflow file."),
    (r"Step canceled by GitHub", "cancelled", "decisive", "Step cancelled by GitHub", "The runner provider reports GitHub cancelled the step; check the run page for a newer push, a sibling failure or a manual cancel, then re-run."),
    (r"Unexpected input\(s\) ('[^']+'(?:, '[^']+')*)", "config", "decisive", "Workflow passes inputs the action does not accept: {0}", "Rename or remove those keys under with:; the warning lists the valid input names."),
    (r'^(?:##\[error\])?"([\w-]+)" (?:length must|must be|is required|is not allowed)', "config", "decisive", "Action input \"{0}\" failed validation", "Fix the value passed to that input under with:."),
    (r"no matching workflow run found with any artifacts", "config", "decisive", "Artifact download found no matching workflow run", "The producing workflow uploaded nothing for this commit (often a skipped fork run); check the workflow_run wiring and allow-forks settings."),
    (r"EBUSY: resource busy or locked", "infra", "strong", "A file on the runner was busy or locked (EBUSY)", "Another process held the file; re-run, or avoid parallel writers to the same path."),
]
_COMPILED = [(re.compile(p, re.M), m, w, s, h) for p, m, w, s, h in RULES]
_NET = re.compile(r"\b(?:ENOTFOUND|EAI_AGAIN|ECONNRESET|ETIMEDOUT|ECONNREFUSED|ECANCELED)\b|Could not resolve host|Temporary failure in name resolution|TLS handshake timeout|Connection (?:timed out|reset by peer)")


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    out = []
    for pat, mode, weight, summary, hint in _COMPILED:
        hit = find_last(lines, pat) if mode == "cancelled" else find_first(lines, pat)
        if not hit:
            continue
        l, m = hit
        g = m.group(1) if m.groups() and m.group(1) else ""
        msg = l.text.replace("##[error]", "", 1).strip()
        out.append(obs(mode, weight, NAME, summary.format(g), [l], errors=[ExtractedError(trunc(msg), l.index + 1)], signals=[trunc(msg, 160)], hint=hint))
    net = find_first(lines, _NET)
    if net:
        l, m = net
        if ctx.step_kind in ("install", "build", "other", "deploy"):
            out.append(obs("infra", "strong", NAME, f"Network error during {ctx.step_kind}: {m.group(0)}", [l], errors=[ExtractedError(trunc(l.text.strip()), l.index + 1)], hint="A registry or API fetch failed; retry, or pin a mirror if it recurs."))
        else:
            out.append(obs("test_assertion", "medium", NAME, f"Network error inside tests: {m.group(0)}", [l], errors=[ExtractedError(trunc(l.text.strip()), l.index + 1)], hint="A test hit the network; mock the call or mark it as an integration test."))
    return out
