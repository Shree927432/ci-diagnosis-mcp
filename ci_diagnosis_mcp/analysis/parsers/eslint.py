"""eslint, biome, stylelint."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, dedupe_errors, find_last, obs, plural, trunc

NAME = "eslint"
GATE = re.compile(r"""^✖ \d+ problems?|^\s+\d+:\d+\s+(?:error|warning)\s|lint/[\w/]+""", re.M)
_FILE = re.compile(r"^((?:/|\.{0,2}/)?[\w./@-]+\.(?:[cm]?[jt]sx?|vue|svelte|astro))$")
_ROW = re.compile(r"^\s+(\d+):(\d+)\s+(error|warning)\s+(.*?)\s{2,}(\S+)\s*$")
_SUMMARY = re.compile(r"^✖ (\d+) problems? \((\d+) errors?, (\d+) warnings?\)")
_BIOME = re.compile(r"^((?:\.{0,2}/)?[\w./@-]+\.(?:[cm]?[jt]sx?|json|css)):(\d+):(\d+)\s+(lint/[\w/]+|format|organizeImports|parse)\s")
_BIOME_SUMMARY = re.compile(r"^Found (\d+) errors?\.?$")


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    errors, hits, warnings, current, biome = [], [], 0, None, False
    for l in lines:
        f = _FILE.match(l.text)
        if f:
            current = f.group(1)
            continue
        r = _ROW.match(l.text)
        if r and current:
            if r.group(3) == "warning":
                warnings += 1
                continue
            errors.append(ExtractedError(trunc(r.group(4)), l.index + 1, current, int(r.group(1)), int(r.group(2)), rule=r.group(5)))
            hits.append(l)
            continue
        b = _BIOME.match(l.text)
        if b:
            biome = True
            errors.append(ExtractedError(f"biome {b.group(4)}", l.index + 1, b.group(1), int(b.group(2)), int(b.group(3)), rule=b.group(4)))
            hits.append(l)
    summary = find_last(lines, _SUMMARY) or (find_last(lines, _BIOME_SUMMARY) if biome else None)
    if not errors and not summary:
        return []
    if summary and summary[1].re is _SUMMARY and int(summary[1].group(2)) == 0:
        return []
    if summary:
        hits.append(summary[0])
    errors = dedupe_errors(errors)
    tool = "biome" if biome else "eslint"
    n = int(summary[1].group(2) if summary and summary[1].re is _SUMMARY else summary[1].group(1)) if summary else len(errors)
    rules = ", ".join(dict.fromkeys(e.rule for e in errors if e.rule))[:80]
    return [obs("lint_format", "decisive" if summary else "strong", NAME, f"{tool}: {plural(n, 'lint error')}" + (f" ({rules})" if rules else ""), hits, anchor=hits[0].index,
                errors=errors, signals=[summary[0].text.strip()] if summary else [], hint="Many rules are auto-fixable (run the linter with --fix); otherwise edit the listed file:line.")]
