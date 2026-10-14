from ci_diagnosis_mcp.belief.actions import ACTIONS, Context, availability, rank, recommendation, voi
from ci_diagnosis_mcp.belief.fixes import all_cells_nonempty, suggested_actions


def ctx(**kw):
    base = dict(mode="test_assertion", mode_confidence="high", posterior={"this_change": 0.55, "flaky": 0.18, "environment": 0.12, "external": 0.10, "pre_existing": 0.05},
                origin_confidence="low", channels_observed=[], taken=set(), has_pr=True, job_count=4, attempt=1, attempt_count=2)
    base.update(kw)
    return Context(**base)


def test_every_fix_matrix_cell_is_nonempty():
    assert all_cells_nonempty() and suggested_actions("unknown", "flaky")


def test_availability_rules():
    a = availability(ctx(has_pr=False, job_count=1, attempt_count=1, taken={"fetch_annotations"}))
    assert a["fetch_pr_diff"] and a["fetch_pr_history"] and a["fetch_sibling_jobs"] and a["fetch_attempts"] and a["fetch_annotations"] == "already taken"
    assert a["fetch_base_history"] is None and a["fetch_full_step_log"] is None


def test_voi_is_zero_when_mass_is_settled_and_divides_by_cost():
    settled = {"this_change": 1.0, "flaky": 0.0, "environment": 0.0, "external": 0.0, "pre_existing": 0.0}
    assert voi("fetch_pr_diff", settled) == 0.0
    split = {"this_change": 0.5, "flaky": 0.5, "environment": 0.0, "external": 0.0, "pre_existing": 0.0}
    assert voi("fetch_pr_history", split) > voi("fetch_pr_diff", split)  # same split, lower cost ranks higher


def test_overrides_put_diff_first_for_this_change_and_full_log_first_for_unknown():
    top, _ = rank(ctx())
    assert top[0]["action"] == "fetch_pr_diff"
    top, _ = rank(ctx(mode="unknown", mode_confidence="low"))
    assert top[0]["action"] == "fetch_full_step_log"
    top, unavailable = rank(ctx(has_pr=False))
    assert all(d["action"] not in ("fetch_pr_diff", "fetch_pr_history") for d in top) and {u["action"] for u in unavailable} == {"fetch_pr_diff", "fetch_pr_history"}
    assert len(top) <= 3


def test_stop_precedence():
    # 1. action-equivalence: lint_format is fixed the same way whatever the origin.
    c = ctx(mode="lint_format", posterior={"this_change": 0.5, "flaky": 0.45, "environment": 0.03, "external": 0.01, "pre_existing": 0.01})
    assert recommendation(c, rank(c)[0]) == ("stop", "action_equivalent")
    # 2. two channels, one non-free, high confidence
    c = ctx(channels_observed=["matrix_pattern", "diff_overlap"], origin_confidence="high", posterior={"this_change": 0.9, "flaky": 0.05, "environment": 0.03, "external": 0.01, "pre_existing": 0.01}, taken={"fetch_pr_diff"})
    assert recommendation(c, rank(c)[0]) == ("stop", "origin_confident")
    # only free channels observed: keep gathering even if confident
    c = ctx(channels_observed=["matrix_pattern", "attempts"], origin_confidence="high", posterior={"this_change": 0.9, "flaky": 0.05, "environment": 0.03, "external": 0.01, "pre_existing": 0.01})
    assert recommendation(c, rank(c)[0])[0] == "gather"
    # 3. no actions available and mode not low
    c = ctx(has_pr=False, job_count=1, attempt_count=1, taken={"fetch_annotations", "fetch_full_step_log", "fetch_base_history"})
    assert recommendation(c, rank(c)[0]) == ("stop", "no_actions_available")
    # mode low: never stop on equivalence
    c = ctx(mode="unknown", mode_confidence="low")
    assert recommendation(c, rank(c)[0])[0] == "gather"
