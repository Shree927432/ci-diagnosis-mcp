"""Log normalisation and rendering: ANSI/timestamp stripping, workflow commands, noise, excerpts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .types import LogLine

MAX_LINE = 2000  # FR-3: lines are truncated before any regex sees them

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][AB012]")
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z\s?")
_BRACKET_CMD = re.compile(r"^##\[(error|warning|notice|group|endgroup|command|debug|section)\](.*)$")
_COLON_CMD = re.compile(r"^::(error|warning|notice|group|endgroup|debug)(?:\s+([^:]*))?::(.*)$")
_EXIT = re.compile(r"Process completed with exit code (\d+)")

_NOISE = [
    re.compile(p)
    for p in (
        r"^##\[debug\]",
        r"^[-=_*#]{4,}$",
        r"^\s*\d+%\s*\|",
        r"^\s*(?:Receiving|Resolving|Compressing|Counting|Unpacking) objects:",
        r"^\s*remote: (?:Counting|Compressing|Total|Enumerating)",
        r"^\s*Progress \(\d+\):",
        r"^\s*\[\s*\d+/\d+\]\s*$",
        r"^\s*Downloading\s+\S+\s+\(\d+(?:\.\d+)?\s*[kM]B\)\s*$",
    )
]

# Pre-filter (NFR-1): parsers only see lines that look relevant plus a window around them.
CANDIDATE = re.compile(
    r"##\[(?:error|warning|group)\]|^::(?:error|warning)|Process completed with exit code"
    r"|\b(?:error|Error|ERROR|FAIL|FAILED|Failed|failed|fatal|FATAL|panic|Traceback|Exception|AssertionError"
    r"|warning: unused|undefined|Cannot find|not found|No such file|Killed|timed out|canceled|cancelled"
    r"|ENOENT|ECONN|ETIMEDOUT|ERESOLVE|E\d{3}\b|TS\d{4}|npm (?:ERR!|error)|ERR_PNPM|lost communication"
    r"|out of memory|heap|No space left|rate limit|Tests?:|Test Suites:|Test Files|passed|failing|Expected|Received|expect\()\b"
    r"|^\s*(?:●|✕|✖|×|❯|---\s+FAIL|FAIL\s|=+\s|_{3,}\s|E\s{3}|File \"|\s+at\s|\s+-->\s)"
    r"|^\S+\.(?:[cm]?[jt]sx?|py|go|rs|java|kt)[:(]\d+"
)


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def parse_lines(raw: str) -> list[LogLine]:
    """Split a raw log into LogLines. '\\r' progress output keeps only the last segment."""
    out: list[LogLine] = []
    raw = raw.replace("\r\n", "\n")
    if raw.endswith("\n"):
        raw = raw[:-1]
    for i, line in enumerate(raw.split("\n")):
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1]
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE]
        ts = None
        m = _TS.match(line)
        if m:
            try:
                micro = int((m.group(2) or "0")[:6].ljust(6, "0"))
                ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(microsecond=micro, tzinfo=timezone.utc)
            except ValueError:
                ts = None
            line = line[m.end():]
        out.append(LogLine(i, ts, strip_ansi(line).rstrip()))
    return out


@dataclass(slots=True)
class WorkflowCommand:
    kind: str
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None


def parse_workflow_command(text: str) -> WorkflowCommand | None:
    m = _BRACKET_CMD.match(text)
    if m:
        return WorkflowCommand(m.group(1), m.group(2).strip())
    m = _COLON_CMD.match(text)
    if m:
        props: dict[str, str] = {}
        for kv in (m.group(2) or "").split(","):
            k, _, v = kv.partition("=")
            if k.strip() and v:
                props[k.strip()] = v.strip()
        cmd = WorkflowCommand(m.group(1), m.group(3).strip(), props.get("file"))
        if props.get("line", "").isdigit():
            cmd.line = int(props["line"])
        if props.get("col", "").isdigit():
            cmd.column = int(props["col"])
        return cmd
    return None


def exit_code(text: str) -> int | None:
    m = _EXIT.search(text)
    return int(m.group(1)) if m else None


def is_error_line(text: str) -> bool:
    """A line that proves a step failed: an error command or the exit-code line (used to verify slices)."""
    cmd = parse_workflow_command(text)
    return (cmd is not None and cmd.kind == "error") or exit_code(text) is not None


def is_noise(text: str) -> bool:
    return not text.strip() or any(p.match(text) for p in _NOISE)


def is_candidate(text: str) -> bool:
    return bool(CANDIDATE.search(text))


def candidate_lines(lines: list[LogLine], window: int = 6) -> list[LogLine]:
    """Lines that match the pre-filter plus `window` lines around each; order preserved, no duplicates."""
    keep = set()
    n = len(lines)
    for i, l in enumerate(lines):
        if is_candidate(l.text):
            keep.update(range(max(0, i - window), min(n, i + window + 1)))
    return [lines[i] for i in sorted(keep)]


def render(lines: list[LogLine], flagged: set[int] | None = None) -> str:
    """Agent-readable text: original line numbers, workflow commands rewritten, noise dropped, errors marked."""
    flagged = flagged or set()
    out: list[str] = []
    blank = False
    for l in lines:
        text = l.text
        cmd = parse_workflow_command(text)
        if cmd:
            if cmd.kind in ("debug", "endgroup"):
                continue
            prefix = {"group": "── ", "section": "── ", "command": "$ ", "error": "ERROR: ", "warning": "WARN: ", "notice": "NOTE: "}[cmd.kind]
            text = prefix + cmd.message
            if cmd.file:
                text += f" [{cmd.file}{':' + str(cmd.line) if cmd.line else ''}]"
        elif is_noise(text):
            if not text.strip() and not blank:
                out.append("")
                blank = True
            continue
        blank = False
        mark = ">>" if l.index in flagged else "  "
        out.append(f"{mark} L{l.index + 1}: {text}")
    return "\n".join(out).strip()


def clamp(text: str, max_chars: int, anchor_line: int | None = None) -> str:
    """Trim rendered text to max_chars, keeping the region around the anchor (favouring lines after it)."""
    if len(text) <= max_chars:
        return text
    lines = text.split("\n")
    center = len(lines) // 2
    if anchor_line is not None:
        tag = f" L{anchor_line + 1}: "
        center = next((i for i, l in enumerate(lines) if tag in l), center)
    lo, hi, size, kept = center, center, 0, []
    while size < max_chars and (lo >= 0 or hi < len(lines)):
        moved = False
        for _ in range(2):  # two after, one before
            if hi < len(lines) and size + len(lines[hi]) + 1 <= max_chars:
                kept.append(lines[hi]); size += len(lines[hi]) + 1; hi += 1; moved = True
        if lo > 0 and size + len(lines[lo - 1]) + 1 <= max_chars:
            lo -= 1; kept.insert(0, lines[lo]); size += len(lines[lo]) + 1; moved = True
        if not moved:
            break
    head = f"… ({lo} lines before omitted)\n" if lo > 0 else ""
    tail = f"\n… ({len(lines) - hi} lines after omitted)" if hi < len(lines) else ""
    return head + "\n".join(kept) + tail


def fence(text: str, label: str = "untrusted log data") -> str:
    """Wrap text in a code fence longer than any backtick run inside it (FR-3)."""
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}text {label}\n{text}\n{ticks}"


def excerpt(lines: list[LogLine], anchor: int, flagged: set[int] | None = None, before: int = 20, after: int = 60, max_chars: int = 6000) -> str:
    """Rendered, clamped, fenced window around `anchor` (an original 0-based line index)."""
    pos = next((i for i, l in enumerate(lines) if l.index >= anchor), len(lines) - 1)
    chunk = lines[max(0, pos - before): pos + after + 1]
    return fence(clamp(render(chunk, flagged), max_chars, anchor))
