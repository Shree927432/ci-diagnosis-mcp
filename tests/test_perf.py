"""NFR-1 timing (AC-7). NFR-2/NFR-3 replay timing and memory are added with the investigation layer."""
import random
import time

from ci_diagnosis_mcp.analysis.pipeline import analyze


def big_log(target_mb: int = 10, seed: int = 1) -> str:
    rnd = random.Random(seed)
    noise = ["Compiling module %d of 900" % i for i in range(900)] + ["Downloading https://cdn.example.com/pkg-%d.tgz" % i for i in range(300)]
    cand = ["src/mod%d.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'." % i for i in range(200)] + ["Error: something failed in step %d" % i for i in range(100)]
    rows, size = [], 0
    while size < target_mb * 1024 * 1024:
        line = rnd.choice(cand) if rnd.random() < 0.06 else rnd.choice(noise)  # >= 5 % candidate lines
        rows.append("2026-10-15T10:00:30.0000000Z " + line)
        size += len(line) + 30
    rows.append("2026-10-15T10:00:40.0000000Z ##[error]Process completed with exit code 2.")
    return "\n".join(rows) + "\n"


def test_analyze_10mb_under_2s():
    raw = big_log()
    assert len(raw) >= 10 * 1024 * 1024
    t = time.perf_counter()
    a = analyze(raw)
    dt = time.perf_counter() - t
    print(f"\n10MB analyze: {dt:.2f}s lines={a.total_lines} mode={a.mode}")
    assert dt < 2.0
    assert a.mode == "compile"


# ---- NFR-2 / NFR-3: investigation replay timing and peak memory -------------
import asyncio
import json
import pathlib
import resource
import sys

import pytest

from ci_diagnosis_mcp import investigation as inv
from ci_diagnosis_mcp.state import State
from tests.fake_github import mk_run

FIX = pathlib.Path(__file__).parent / "data" / "jest-assert"


def padded_log(target_mb: int) -> str:
    """A realistic failed-step log padded to target_mb with noisy build output before the failure."""
    head, body = (FIX / "job.log").read_text().split("##[group]Run npm test", 1)
    block = "".join(f"2026-10-15T10:00:2{i % 9}.0000000Z Compiling module {i} of 9000 ... done\n" for i in range(2000))  # ~130 KB
    pad = block * (target_mb * 1024 * 1024 // len(block) + 1)
    return head + pad + "##[group]Run npm test" + body


def run_with_jobs(n_jobs: int, mb: int):
    job = json.loads((FIX / "job.json").read_text())
    # the padding lands inside the install step window, so widen that step's recorded times to match
    jobs = [{**job, "id": 5000 + i, "name": f"test (shard {i})", "run_id": 100, "conclusion": "failure", "html_url": ""} for i in range(n_jobs)]
    return mk_run(100), jobs, {j["id"]: (lambda: padded_log(mb)) for j in jobs}


def rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


@pytest.mark.asyncio
async def test_start_investigation_4x10mb_under_20s(tmp_path):
    from tests.fake_github import FakeGitHub
    run, jobs, logs = run_with_jobs(4, 10)
    gh = FakeGitHub(runs=[run], jobs={(100, 1): jobs}, logs=logs)
    s = State(tmp_path)
    t = time.perf_counter()
    r = await inv.start(gh, s, "acme/app", run_id=100)
    dt = time.perf_counter() - t
    print(f"\n4x10MB start_investigation: {dt:.1f}s mode={r['mode']} clusters={len(r['clusters'])}")
    assert dt < 20 and r["mode"] == "test_assertion"
    s.close()


def test_peak_memory_8x20mb_under_500mb(tmp_path):
    """Measured in a fresh interpreter so earlier tests' allocations do not count (ru_maxrss is a process high-water mark)."""
    import subprocess
    code = f"""
import asyncio, resource, sys
sys.path.insert(0, {str(pathlib.Path(__file__).parent.parent)!r})
from tests.test_perf import run_with_jobs, rss_mb
from tests.fake_github import FakeGitHub
from ci_diagnosis_mcp import investigation as inv
from ci_diagnosis_mcp.state import State
run, jobs, logs = run_with_jobs(8, 20)
gh = FakeGitHub(runs=[run], jobs={{(100, 1): jobs}}, logs=logs)
s = State(__import__('pathlib').Path({str(tmp_path)!r}))
r = asyncio.run(inv.start(gh, s, 'acme/app', run_id=100))
print(f"PEAK {{rss_mb():.0f}} MODE {{r['mode']}}")
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    line = next(l for l in out.stdout.splitlines() if l.startswith("PEAK")), out.stderr
    peak = float(line[0].split()[1])
    print(f"\n8x20MB peak RSS in fresh process: {peak:.0f} MB")
    assert peak < 500, line
    assert "MODE test_assertion" in line[0]
