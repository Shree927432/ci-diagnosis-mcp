"""SQLite state and log cache (SPEC NFR-6, FR-14, FR-15)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (id TEXT PRIMARY KEY, repo TEXT, run_id INTEGER, attempt INTEGER, created REAL, updated REAL, data TEXT);
CREATE TABLE IF NOT EXISTS evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, investigation TEXT, channel TEXT, source TEXT, observation TEXT, weights TEXT, log_lines TEXT, created REAL);
CREATE TABLE IF NOT EXISTS channels_observed (investigation TEXT, channel TEXT, observed_at REAL, PRIMARY KEY (investigation, channel));
CREATE TABLE IF NOT EXISTS outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, investigation TEXT, actual_mode TEXT, actual_origin TEXT, derived_origin TEXT, resolved_by TEXT, fix_summary TEXT, fix_commit TEXT, fix_files TEXT, evidence_snapshot TEXT, channels TEXT, created REAL);
"""


def state_dir() -> Path:
    os.umask(0o077)
    d = Path(os.environ.get("CI_DIAGNOSIS_STATE") or Path.home() / ".ci-diagnosis-mcp")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    (d / "logs").mkdir(exist_ok=True)
    return d


def validate_repo(repo: str) -> str:
    """owner/name with safe characters; rejects path traversal such as ../x."""
    if not REPO_RE.match(repo or "") or any(part.strip(".") == "" for part in repo.split("/")):
        raise ValueError(f"invalid repo {repo!r}: expected owner/name")
    return repo


class State:
    def __init__(self, directory: Path | None = None, ttl_days: int = 7):
        self.dir = directory or state_dir()
        self.db = sqlite3.connect(self.dir / "state.db", timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self.purge_logs(ttl_days)

    # ---- investigations ---------------------------------------------------
    def save(self, inv_id: str, repo: str, run_id: int, attempt: int, data: dict) -> None:
        now = time.time()
        self.db.execute("INSERT INTO investigations(id, repo, run_id, attempt, created, updated, data) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                        (inv_id, repo, run_id, attempt, now, now, json.dumps(data)))
        self.db.commit()

    def load(self, inv_id: str) -> dict | None:
        row = self.db.execute("SELECT data FROM investigations WHERE id=?", (inv_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def siblings(self, repo: str, run_id: int) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT id FROM investigations WHERE repo=? AND run_id=? ORDER BY attempt", (repo, run_id))]

    # ---- evidence ---------------------------------------------------------
    def add_evidence(self, inv_id: str, channel: str, observation: str, weights: dict, source: str = "server", log_lines: list[int] | None = None) -> int:
        cur = self.db.execute("INSERT INTO evidence(investigation, channel, source, observation, weights, log_lines, created) VALUES(?,?,?,?,?,?,?)",
                              (inv_id, channel, source, observation, json.dumps(weights), json.dumps(log_lines or []), time.time()))
        self.db.execute("INSERT OR IGNORE INTO channels_observed(investigation, channel, observed_at) VALUES(?,?,?)", (inv_id, channel, time.time()))
        self.db.commit()
        return int(cur.lastrowid)

    def mark_channel(self, inv_id: str, channel: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO channels_observed(investigation, channel, observed_at) VALUES(?,?,?)", (inv_id, channel, time.time()))
        self.db.commit()

    def evidence(self, inv_id: str) -> list[dict]:
        rows = self.db.execute("SELECT id, channel, source, observation, weights, log_lines, created FROM evidence WHERE investigation=? ORDER BY id", (inv_id,))
        return [{"id": r[0], "channel": r[1], "source": r[2], "observation": r[3], "weights": json.loads(r[4]), "log_lines": json.loads(r[5]), "created": r[6]} for r in rows]

    def channels(self, inv_id: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT channel FROM channels_observed WHERE investigation=? ORDER BY channel", (inv_id,))]

    # ---- outcomes ---------------------------------------------------------
    def record_outcome(self, inv_id: str, actual_mode: str, actual_origin: str, derived: str | None, resolved_by: str | None, fix_summary: str, fix_commit: str | None, fix_files: list[str]) -> int:
        snap = json.dumps({"evidence": self.evidence(inv_id), "channels": self.channels(inv_id)})
        cur = self.db.execute("INSERT INTO outcomes(investigation, actual_mode, actual_origin, derived_origin, resolved_by, fix_summary, fix_commit, fix_files, evidence_snapshot, channels, created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (inv_id, actual_mode, actual_origin, derived, resolved_by, fix_summary, fix_commit, json.dumps(fix_files), snap, json.dumps(self.channels(inv_id)), time.time()))
        self.db.commit()
        return int(cur.lastrowid)

    def outcome_count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0])

    # ---- log cache --------------------------------------------------------
    def log_path(self, job_id: int) -> Path:
        return self.dir / "logs" / f"{int(job_id)}.log"

    def cache_log(self, job_id: int, text: str) -> Path:
        p = self.log_path(job_id)
        p.write_text(text)
        os.chmod(p, 0o600)
        return p

    def cached_log(self, job_id: int) -> str | None:
        p = self.log_path(job_id)
        return p.read_text() if p.exists() else None

    def purge_logs(self, ttl_days: int) -> int:
        cutoff, n = time.time() - ttl_days * 86400, 0
        for p in (self.dir / "logs").glob("*.log"):
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        return n

    def close(self) -> None:
        self.db.close()
