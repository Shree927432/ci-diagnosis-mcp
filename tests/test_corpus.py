"""AC-2 / NFR-9: mode accuracy and unknown rate over the hash-locked fixture corpus; AC-3 slice assertions."""
import json
import pathlib
import subprocess

import pytest

from ci_diagnosis_mcp.analysis.pipeline import analyze

ROOT = pathlib.Path(__file__).parent / "fixtures"
CASES = sorted(d for d in ROOT.iterdir() if d.is_dir())


def run(d):
    return analyze((d / "job.log").read_text(), json.loads((d / "job.json").read_text())), json.loads((d / "expected.json").read_text())


@pytest.mark.parametrize("d", CASES, ids=[d.name for d in CASES])
def test_case_details(d):
    a, e = run(d)
    assert a.slice_window == e["slice"]["window"], (a.slice_window, e["slice"])
    assert a.slice_mismatch == e["slice"]["mismatch"]
    if a.mode == e["mode"]:
        assert a.parser == e["parser"], (a.parser, a.summary)
        blob = " | ".join(x.message for x in a.errors) + " | " + " | ".join(a.signals) + " | " + a.summary
        for s in e["errors_contain"]:
            assert s in blob, (s, blob[:300])
        for t in e["tests_contain"]:
            assert any(t in ft.name for ft in a.failed_tests), ([ft.name for ft in a.failed_tests], t)
        for f in e["files_contain"]:
            assert any(f == r.path for r in a.files), ([r.path for r in a.files], f)
        assert a.log_excerpt.startswith("```") and "untrusted log data" in a.log_excerpt.split("\n", 1)[0]


def test_corpus_accuracy_and_unknown_rate():
    results = [(run(d)[0].mode, run(d)[1]["mode"], d.name) for d in CASES]
    correct = sum(1 for got, exp, _ in results if got == exp)
    false_unknown = sum(1 for got, exp, _ in results if got == "unknown" and exp != "unknown")
    wrong = [(n, got, exp) for got, exp, n in results if got != exp]
    acc, unk = correct / len(results), false_unknown / len(results)
    print(f"\naccuracy={acc:.3f} unknown_rate={unk:.3f} n={len(results)} wrong={wrong}")
    assert acc >= 0.90, wrong
    assert unk <= 0.15, wrong


def test_fixtures_were_committed_before_parsers():
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT.parent.parent, capture_output=True, text=True).stdout.strip()
    if not git("rev-parse", "--is-inside-work-tree"):
        pytest.skip("not a git repo")
    first_fixtures = git("log", "--diff-filter=A", "--format=%H", "--reverse", "--", "tests/fixtures").split("\n")[0]
    first_parsers = git("log", "--diff-filter=A", "--format=%H", "--reverse", "--", "ci_diagnosis_mcp/analysis/parsers").split("\n")[0]
    assert first_fixtures, "fixtures not committed"
    if first_parsers:
        assert subprocess.run(["git", "merge-base", "--is-ancestor", first_fixtures, first_parsers], cwd=ROOT.parent.parent).returncode == 0
        assert first_fixtures != first_parsers
