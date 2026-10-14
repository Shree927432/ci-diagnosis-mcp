"""pip, poetry, uv, pipenv."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, find_first, following, obs, pos_of, trunc

NAME = "pip"
RULES = [
    (r"ERROR: Could not find a version that satisfies the requirement (\S+)", "dependency", "decisive", "pip: no version satisfies requirement {0}", "The pinned version does not exist for this Python version/platform, or the name is misspelled.", "pip"),
    (r"ERROR: No matching distribution found for (\S+)", "dependency", "decisive", "pip: no matching distribution for {0}", "No wheel/sdist matches this Python version or platform; loosen the pin or change the interpreter version.", "pip"),
    (r"ResolutionImpossible|The conflict is caused by:", "dependency", "decisive", "pip: dependency resolution conflict", "Two requirements pin incompatible versions; the lines after 'The conflict is caused by:' name them.", "pip"),
    (r"error: subprocess-exited-with-error|Failed building wheel for (\S+)|error: metadata-generation-failed", "compile", "strong", "pip: failed to build {0} from source", "A native extension failed to compile: missing system library/compiler on the runner or no wheel for this Python version.", "pip"),
    (r"SolverProblemError|version solving failed", "dependency", "decisive", "poetry: version solving failed", "Poetry could not find compatible versions; read the 'Because ...' chain.", "poetry"),
    (r"pyproject\.toml changed significantly since poetry\.lock|poetry\.lock is not consistent", "dependency", "decisive", "poetry.lock is out of date", "Run `poetry lock` locally and commit poetry.lock.", "poetry"),
    (r"No solution found when resolving dependencies", "dependency", "decisive", "uv: no solution found when resolving dependencies", "uv found conflicting constraints; the lines after the error explain which requirements collide.", "uv"),
    (r"The lockfile at `uv\.lock` needs to be updated", "dependency", "decisive", "uv.lock is out of date", "Run `uv lock` locally and commit uv.lock.", "uv"),
    (r"Pipfile\.lock .*out[- ]of[- ]date", "dependency", "decisive", "Pipfile.lock is out of date", "Run `pipenv lock` and commit Pipfile.lock.", "pipenv"),
]
_COMPILED = [(re.compile(p), m, w, s, h, t) for p, m, w, s, h, t in RULES]
_FILES = {"pip": "requirements.txt", "poetry": "pyproject.toml", "uv": "pyproject.toml", "pipenv": "Pipfile"}


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    for pat, mode, weight, summary, hint, tool in _COMPILED:
        hit = find_first(lines, pat)
        if not hit:
            continue
        l, m = hit
        g = (m.group(1) if m.groups() and m.group(1) else "a package")
        block = following(lines, pos_of(lines, l.index), 6)
        return [obs(mode, weight, NAME, summary.format(g), [l], errors=[ExtractedError(trunc(" | ".join([l.text.strip()] + block)), l.index + 1, _FILES[tool])], signals=[trunc(l.text.strip(), 160)], hint=hint)]
    return []
