import os
import stat
import time

import pytest

from ci_diagnosis_mcp.state import State, state_dir, validate_repo


@pytest.fixture
def sdir(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_DIAGNOSIS_STATE", str(tmp_path / "st"))
    return state_dir()


def test_directory_and_files_are_private(sdir):
    s = State(sdir)
    s.save("a/b#1@1", "a/b", 1, 1, {"x": 1})
    s.add_evidence("a/b#1@1", "matrix_pattern", "obs", {"flaky": "weak"})
    assert stat.S_IMODE(os.stat(sdir).st_mode) == 0o700
    for name in ("state.db", "state.db-wal"):
        assert stat.S_IMODE(os.stat(sdir / name).st_mode) == 0o600, name
    p = s.cache_log(12345, "log text")
    assert p.name == "12345.log" and stat.S_IMODE(p.stat().st_mode) == 0o600 and s.cached_log(12345) == "log text"
    s.close()


def test_repo_validation():
    assert validate_repo("acme/app") == "acme/app"
    for bad in ("../x", "a/b/c", "", "a"):
        with pytest.raises(ValueError):
            validate_repo(bad)


def test_round_trip_and_trail(sdir):
    s = State(sdir)
    s.save("a/b#9@1", "a/b", 9, 1, {"mode": "compile"})
    s.save("a/b#9@2", "a/b", 9, 2, {"mode": "compile"})
    eid = s.add_evidence("a/b#9@1", "diff_overlap", "files in diff", {"this_change": "strong"}, log_lines=[3, 4])
    assert s.load("a/b#9@1") == {"mode": "compile"} and s.load("nope") is None
    assert s.channels("a/b#9@1") == ["diff_overlap"] and s.evidence("a/b#9@1")[0]["id"] == eid
    assert s.siblings("a/b", 9) == ["a/b#9@1", "a/b#9@2"]
    s.record_outcome("a/b#9@1", "compile", "this_change", "this_change", "code_fix", "fixed", "abc", ["src/a.ts"])
    assert s.outcome_count() == 1
    s.close()


def test_log_cache_ttl_purges_old_files(sdir):
    s = State(sdir)
    old = s.cache_log(1, "old")
    os.utime(old, (time.time() - 8 * 86400, time.time() - 8 * 86400))
    s.cache_log(2, "new")
    assert s.purge_logs(7) == 1 and s.cached_log(1) is None and s.cached_log(2) == "new"
    s.close()
