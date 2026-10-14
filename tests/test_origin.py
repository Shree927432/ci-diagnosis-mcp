import math

from ci_diagnosis_mcp.belief.origin import (
    GLOBAL_PRIOR, Evidence, attempts_evidence, base_history_evidence, channels_observed, confidence, diff_evidence, matrix_evidence, posterior, pr_history_evidence,
)
from ci_diagnosis_mcp.belief.outcome import derive_origin


def test_no_evidence_returns_prior():
    assert posterior([]) == {k: round(v, 4) for k, v in sorted(GLOBAL_PRIOR.items(), key=lambda kv: -kv[1])}
    assert channels_observed([]) == []


def test_within_channel_max_of_supports_times_min_of_againsts():
    ev = [Evidence("c", "a", {"flaky": "strong"}), Evidence("c", "b", {"flaky": "medium"}), Evidence("c", "c", {"flaky": "against"})]
    p = posterior(ev, t=1.0)
    expected = GLOBAL_PRIOR["flaky"] * 8 * (1 / 3)
    others = sum(v for k, v in GLOBAL_PRIOR.items() if k != "flaky")
    assert math.isclose(p["flaky"], expected / (expected + others), rel_tol=1e-3)


def test_channels_multiply_and_tempering_softens():
    ev = [Evidence("a", "x", {"flaky": "strong"}), Evidence("b", "y", {"flaky": "strong"})]
    assert posterior(ev, t=1.0)["flaky"] > posterior(ev, t=0.7)["flaky"] > posterior([])["flaky"]


def test_confidence_bands():
    assert confidence({"flaky": 0.9, "this_change": 0.05})[0] == "high"
    assert confidence({"flaky": 0.6, "this_change": 0.3})[0] == "medium"
    assert confidence({"flaky": 0.4, "this_change": 0.35})[0] == "low"


def test_evidence_builders():
    assert matrix_evidence(1, 1, True, False) == []
    assert matrix_evidence(4, 1, False, True)[0].weights["environment"] == "strong"
    assert attempts_evidence([{"attempt": 1, "conclusion": "success"}])[0].weights["flaky"] == "strong"
    assert attempts_evidence([{"attempt": 1, "conclusion": "failure", "same_signature": True}])[0].weights["flaky"] == "strong_against"
    assert pr_history_evidence([{"sha": "a", "conclusion": "success"}])[0].weights["this_change"] == "strong"
    assert base_history_evidence(True, False, 0, 20)[0].weights["pre_existing"] == "strong"
    d = diff_evidence("test_assertion", ["src/cart.ts"], ["src/cart.ts"], ["src/cart.test.ts"])
    assert d[0].weights["this_change"] == "strong"
    d = diff_evidence("dependency", ["README.md"], [], [])
    assert d[0].weights["external"] == "strong"
    d = diff_evidence("test_assertion", ["docs/x.md"], ["src/a.py"], [])
    assert d[0].weights["this_change"] == "against"


def test_derive_origin_rules_in_order():
    assert derive_origin("rerun", [], False) == "flaky"
    assert derive_origin("code_fix", ["tests/test_a.py", "src/__tests__/b.test.ts"], False) == "this_change"
    assert derive_origin("config_fix", [".github/workflows/ci.yml"], False) == "environment"
    assert derive_origin("config_fix", [".github/workflows/ci.yml"], True) == "this_change"
    assert derive_origin("code_fix", ["tests/test_a.py", "src/a.py"], False) == "this_change"
    assert derive_origin("ignored", [], False) is None
