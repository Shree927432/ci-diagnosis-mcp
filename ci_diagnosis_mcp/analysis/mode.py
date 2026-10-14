"""Mode aggregation: discover parsers, apply line ownership and step context, pick the mode with a confidence band (FR-6)."""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path

from . import parsers as _pkg
from .parsers._util import ParseContext
from .types import WEIGHT_RATIO, LogLine, Observation

# Tie-break order: earlier wins on equal score.
PRIORITY = ["gha", "tsc", "js_tests", "pytest", "gotest", "eslint", "py_lint", "format", "node", "pip", "traceback", "generic"]
BAND = {"decisive": "high", "strong": "medium", "medium": "low", "weak": "low"}
STEP_PRIOR = {  # step kind -> mode -> multiplier (DESIGN §8 step_context)
    "test": {"test_assertion": 1.5, "test_load": 1.5},
    "lint": {"lint_format": 1.5, "compile": 1.2},
    "build": {"compile": 1.5},
    "install": {"dependency": 1.5, "config": 1.2},
    "deploy": {"config": 1.3},
}


def discover(path: str | Path | None = None) -> list:
    """Import every module in the parsers package (or `path`) that defines NAME and parse()."""
    if path is None:
        mods = [importlib.import_module(f"{_pkg.__name__}.{m.name}") for m in pkgutil.iter_modules(_pkg.__path__) if not m.name.startswith("_")]
    else:
        mods = []
        for p in sorted(Path(path).glob("*.py")):
            if p.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(p.stem, p)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            mods.append(mod)
    found = [m for m in mods if hasattr(m, "NAME") and callable(getattr(m, "parse", None))]
    return sorted(found, key=lambda m: PRIORITY.index(m.NAME) if m.NAME in PRIORITY else len(PRIORITY))


_PARSERS = None


def run_parsers(lines: list[LogLine], ctx: ParseContext) -> list[Observation]:
    global _PARSERS
    if _PARSERS is None:
        _PARSERS = discover()
    out: list[Observation] = []
    for m in _PARSERS:
        out.extend(m.parse(lines, ctx))
    return out


@dataclass(slots=True)
class ModeResult:
    mode: str = "unknown"
    confidence: str = "low"
    runner_up: str | None = None
    margin: float = 1.0
    winner: Observation | None = None
    observations: list[Observation] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


def aggregate(observations: list[Observation], ctx: ParseContext | None = None) -> ModeResult:
    ctx = ctx or ParseContext()
    specific = [o for o in observations if o.parser != "generic"]
    owned = [o.claimed for o in specific]
    kept = [o for o in observations if o.parser != "generic" or not any(lo <= o.anchor <= hi for lo, hi in owned)]
    if not kept:
        return ModeResult()
    prior = STEP_PRIOR.get(ctx.step_kind, {})
    best: dict[str, tuple[float, Observation]] = {}
    for o in kept:
        score = WEIGHT_RATIO[o.weight] * prior.get(o.mode, 1.0)
        cur = best.get(o.mode)
        if cur is None or score > cur[0] or (score == cur[0] and PRIORITY.index(o.parser) < PRIORITY.index(cur[1].parser)):
            best[o.mode] = (score, o)
    ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], PRIORITY.index(kv[1][1].parser) if kv[1][1].parser in PRIORITY else 99))
    (mode, (top, winner)) = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    margin = 1.0 if runner is None else round((top - runner[1][0]) / top, 3)
    conf = BAND[winner.weight]
    if mode == "unknown":
        conf = "low"
    elif conf == "high" and margin < 0.5:
        conf = "medium"
    return ModeResult(mode, conf, runner[0] if runner else None, margin, winner, kept, {m: s for m, (s, _) in best.items()})
