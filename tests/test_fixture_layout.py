"""AC-1 layout checks for the fixture corpus; the corpus accuracy test lives in test_corpus.py (T2b)."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent / "fixtures"
sys.path.insert(0, str(ROOT))
from generate_fixtures import tree_hash  # noqa: E402

V01_MODES = ["test_assertion", "test_load", "compile", "lint_format", "dependency", "runtime", "config", "out_of_memory", "timeout", "cancelled", "infra"]
PARSERS = ["generic", "gha", "js_tests", "pytest", "gotest", "tsc", "eslint", "py_lint", "format", "node", "pip", "traceback"]


def cases():
    return sorted(d for d in ROOT.iterdir() if d.is_dir())


def expected(d):
    return json.loads((d / "expected.json").read_text())


def test_every_case_has_three_files_and_valid_expected():
    for d in cases():
        assert {p.name for p in d.iterdir()} == {"job.log", "job.json", "expected.json"}, d.name
        e = expected(d)
        assert e["mode"] in V01_MODES + ["unknown"] and e["parser"] in PARSERS, d.name
        assert e["slice"]["window"] in ("failed_step", "widened", "whole_log") and isinstance(e["slice"]["mismatch"], bool)
        job = json.loads((d / "job.json").read_text())
        assert any(s["conclusion"] in ("failure", "timed_out", "cancelled") for s in job["steps"]), d.name
        assert (d / "job.log").read_text().count("\n") >= 10


def test_coverage_floors():
    by_mode, by_parser, wrong_ts = {}, {}, 0
    for d in cases():
        e = expected(d)
        by_mode[e["mode"]] = by_mode.get(e["mode"], 0) + 1
        by_parser[e["parser"]] = by_parser.get(e["parser"], 0) + 1
        wrong_ts += e["slice"]["window"] != "failed_step"
    for m in V01_MODES:
        assert by_mode.get(m, 0) >= 2, m
    assert by_mode.get("unknown", 0) >= 3
    for p in PARSERS:
        assert by_parser.get(p, 0) >= 1, p
    assert wrong_ts >= 3


def test_fixture_tree_is_unchanged_since_commit():
    assert (ROOT / "TREE_SHA256").read_text().strip() == tree_hash()
