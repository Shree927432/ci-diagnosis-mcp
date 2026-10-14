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
