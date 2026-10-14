"""Fix matrix: (mode, origin) -> suggested actions (DESIGN §5, SPEC FR-13)."""
from __future__ import annotations

from ..analysis.types import MODES
from .origin import ORIGINS

_BY_MODE = {
    "test_assertion": "Read the failing assertion (expected vs received) and the test's source location.",
    "test_load": "Fix the import or collection error first; assertion failures may be downstream.",
    "compile": "Fix the listed file:line errors in order; later errors are often cascades.",
    "lint_format": "Run the fixer (eslint --fix, ruff --fix, prettier --write, black) locally and commit the result.",
    "dependency": "Check the lockfile and manifest for the named package and version.",
    "runtime": "Open the deepest non-library frame in the stack and inspect the failing value.",
    "config": "Edit the workflow: permissions, secrets, tool versions, inputs, or paths.",
    "out_of_memory": "Reduce parallel workers, raise the heap or memory limit, or use a larger runner.",
    "timeout": "Find the last output before the timeout; it shows what hung.",
    "cancelled": "Diagnose the sibling job that failed first; this job did not fail on its own.",
    "infra": "Rerun the job; if it recurs, authenticate registries or change runner.",
    "unknown": "Read the flagged lines in the excerpt and the failing command above them.",
}
_BY_PAIR = {
    ("test_assertion", "this_change"): ["Compare the failing test with the diff: source changed and test untouched → fix the code or update the expectation; only tests changed → the new test is wrong."],
    ("test_assertion", "flaky"): ["Rerun now; then quarantine or fix the flake (timing, ordering, shared state, network)."],
    ("test_assertion", "pre_existing"): ["Already failing on the base branch; do not block this change on it, link the base failure."],
    ("test_assertion", "environment"): ["The failure is specific to one matrix axis; check OS, runtime version, or locale assumptions in the test."],
    ("test_assertion", "external"): ["An unpinned dependency or external service changed; pin it or mock the call."],
    ("compile", "this_change"): ["Fix the type or syntax errors at the listed locations."],
    ("compile", "external"): ["An unpinned dependency changed its types; pin the version that compiled last."],
    ("dependency", "this_change"): ["The lockfile is out of sync with the manifest; regenerate it locally and commit."],
    ("dependency", "external"): ["A version disappeared or the registry is down; pin or retry later."],
    ("out_of_memory", "this_change"): ["A memory regression; profile the change or shard the work."],
    ("out_of_memory", "environment"): ["Reduce parallelism or raise heap limits for this runner size."],
    ("timeout", "this_change"): ["Likely a hang or deadlock introduced by the change; inspect the last output before the timeout."],
    ("config", "environment"): ["Edit the workflow: permissions block, secrets, setup-* versions, or paths."],
    ("config", "this_change"): ["The workflow change in this PR is wrong; fix the YAML."],
    ("infra", "external"): ["Rerun; if recurrent, authenticate the registry or mirror the image."],
    ("infra", "environment"): ["Rerun on a different runner or free disk before the heavy step."],
}


def suggested_actions(mode: str, origin: str) -> list[str]:
    return _BY_PAIR.get((mode, origin), []) + [_BY_MODE.get(mode, _BY_MODE["unknown"])]


def action_signature(mode: str, origin: str) -> str:
    """Identity used by the action-equivalence stop rule: two origins are equivalent if they lead to the same first suggestion."""
    return suggested_actions(mode, origin)[0]


def all_cells_nonempty() -> bool:
    return all(suggested_actions(m, o) for m in MODES for o in ORIGINS)
