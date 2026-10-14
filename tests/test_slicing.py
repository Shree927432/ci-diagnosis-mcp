from datetime import datetime, timezone

from ci_diagnosis_mcp.analysis.normalize import parse_lines
from ci_diagnosis_mcp.analysis.slicing import Step, failed_step, parse_steps, slice_to_failed_step


def t(sec: int) -> datetime:
    return datetime(2026, 10, 15, 10, 0, sec, tzinfo=timezone.utc)


def log(*rows: tuple[int, str]) -> str:
    return "\n".join(f"2026-10-15T10:00:{s:02d}.0000000Z {text}" for s, text in rows)


RAW = log((0, "##[group]Run checkout"), (1, "done"), (10, "##[group]Run npm test"), (11, "FAIL a.test.ts"), (12, "##[error]Process completed with exit code 1"), (20, "##[group]Post checkout"), (21, "ok"))


def steps(fail_start: int, fail_end: int) -> list[Step]:
    return [Step("checkout", 1, "success", t(0), t(9)), Step("npm test", 2, "failure", t(fail_start), t(fail_end)), Step("post", 3, "success", t(20), t(21))]


def test_window_on_failed_step():
    s = slice_to_failed_step(parse_lines(RAW), steps(10, 12))
    assert s.window == "failed_step" and [l.index for l in s.lines] == [2, 3, 4] and s.failed_step.name == "npm test"


def test_wrong_timestamps_widen_to_previous_step():
    # Failed step's recorded window misses its own output; the previous step's start still covers it.
    s = slice_to_failed_step(parse_lines(RAW), steps(14, 16))
    assert s.window == "widened" and any("exit code 1" in l.text for l in s.lines)


def test_no_error_anywhere_in_windows_falls_back_to_whole_log():
    raw = log((0, "a"), (30, "##[error]late"))
    s = slice_to_failed_step(parse_lines(raw), steps(10, 12))
    assert s.window == "whole_log" and len(s.lines) == 2


def test_missing_timestamps_fall_back_to_whole_log():
    st = [Step("x", 1, "failure", None, None)]
    assert slice_to_failed_step(parse_lines(RAW), st).window == "whole_log"
    assert slice_to_failed_step(parse_lines(RAW), []).window == "whole_log"


def test_failed_step_prefers_failure_over_cancelled():
    st = [Step("a", 1, "cancelled", None, None), Step("b", 2, "failure", None, None)]
    assert failed_step(st).name == "b"
    assert failed_step([Step("a", 1, "cancelled", None, None)]).name == "a"


def test_parse_steps_from_job_json():
    job = {"steps": [{"name": "Run tests", "number": 3, "conclusion": "failure", "started_at": "2026-10-15T10:00:10Z", "completed_at": "2026-10-15T10:00:12Z"}]}
    s = parse_steps(job)[0]
    assert (s.name, s.number, s.conclusion, s.started_at) == ("Run tests", 3, "failure", t(10))
