"""FR-6 mode aggregation: ownership, step_context prior, bands, runner-up, margin; NFR-10 parser discovery."""
import pathlib

from ci_diagnosis_mcp.analysis.mode import aggregate, discover
from ci_diagnosis_mcp.analysis.parsers._util import ParseContext
from ci_diagnosis_mcp.analysis.types import Observation


def ob(mode, weight, parser, anchor=0, claimed=(0, 0)):
    return Observation(mode, weight, parser, f"{parser} {mode}", anchor, claimed)


def test_generic_inside_claimed_range_is_dropped_and_specific_wins():
    r = aggregate([ob("unknown", "weak", "generic", anchor=5, claimed=(5, 9)), ob("compile", "decisive", "tsc", anchor=3, claimed=(2, 8))])
    assert r.mode == "compile" and r.confidence == "high" and all(o.parser != "generic" for o in r.observations)


def test_generic_outside_claimed_range_survives_but_loses():
    r = aggregate([ob("unknown", "weak", "generic", anchor=50, claimed=(50, 50)), ob("runtime", "strong", "traceback", anchor=3, claimed=(2, 8))])
    assert r.mode == "runtime" and r.confidence == "medium" and r.runner_up == "unknown" and 0 < r.margin < 1


def test_generic_alone_is_unknown_low():
    r = aggregate([ob("unknown", "weak", "generic")])
    assert (r.mode, r.confidence, r.runner_up, r.margin) == ("unknown", "low", None, 1.0)


def test_step_context_prior_breaks_ties():
    obs = [ob("test_assertion", "strong", "gha"), ob("dependency", "strong", "pip")]
    assert aggregate(obs, ParseContext("Run pytest")).mode == "test_assertion"
    assert aggregate(obs, ParseContext("pip install -r requirements.txt")).mode == "dependency"


def test_close_decisive_competition_downgrades_to_medium():
    r = aggregate([ob("compile", "decisive", "tsc"), ob("lint_format", "decisive", "eslint")])
    assert r.confidence == "medium" and r.margin == 0.0


def test_discovery_picks_up_a_new_parser_module(tmp_path: pathlib.Path):
    (tmp_path / "zzz_custom.py").write_text("NAME = 'zzz_custom'\ndef parse(lines, ctx):\n    return []\n")
    (tmp_path / "_helper.py").write_text("NAME = 'nope'\n")
    (tmp_path / "broken.py").write_text("X = 1\n")
    names = [m.NAME for m in discover(tmp_path)]
    assert names == ["zzz_custom"]
    assert "generic" in [m.NAME for m in discover()]
