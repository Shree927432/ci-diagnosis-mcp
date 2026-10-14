"""npm / pnpm / yarn package-manager errors, module resolution, Node runtime exceptions."""
from __future__ import annotations

import re

from ..types import ExtractedError, LogLine
from ._util import ParseContext, find_all, find_first, obs, pos_of, trunc

NAME = "node"
GATE = re.compile(r"""^npm (?:ERR!|error)|ERR_PNPM_|Cannot find module|Module not found|Failed to resolve import|ERR_REQUIRE_ESM|ERR_MODULE_NOT_FOUND|ERR_PACKAGE_PATH_NOT_EXPORTED|ERR_UNSUPPORTED_DIR_IMPORT|^(?:Uncaught\s+)?(?:TypeError|ReferenceError|SyntaxError|RangeError|AssertionError)(?: \[\w+\])?: |Missing script:|EBADENGINE|engine \"node\" is incompatible""", re.M)
_NPM_CODE = re.compile(r"^npm (?:ERR!|error) code (\w+)")
_NPM_LINE = re.compile(r"^npm (?:ERR!|error)\s?(.*)$")
_PNPM = re.compile(r"\bERR_PNPM_(\w+)\b")
_CANNOT = re.compile(r"Error: Cannot find module '([^']+)'")
_WEBPACK = re.compile(r"Module not found: (?:Error: )?Can't resolve '([^']+)' in '([^']+)'")
_VITE = re.compile(r"Failed to resolve import \"([^\"]+)\" from \"([^\"]+)\"")
_ESM = re.compile(r"\b(ERR_REQUIRE_ESM|ERR_MODULE_NOT_FOUND|ERR_PACKAGE_PATH_NOT_EXPORTED|ERR_UNSUPPORTED_DIR_IMPORT)\b")
_RUNTIME = re.compile(r"^(?:Uncaught\s+)?(TypeError|ReferenceError|SyntaxError|RangeError|AssertionError)(?: \[(\w+)\])?: (.*)$")
_STACK = re.compile(r"^\s+at\s+(?:.*?\()?((?:file://)?(?:\.{0,2}/)?[\w./@:-]+\.(?:[cm]?[jt]sx?)):(\d+):(\d+)\)?")
_MISSING_SCRIPT = re.compile(r"Missing script: \"?([^\"\s]+)\"?")
_ENGINE = re.compile(r"EBADENGINE|The engine \"node\" is incompatible")
_DEP_CODES = {"ERESOLVE", "ETARGET", "E404", "ENOTFOUND", "EINTEGRITY", "ENOVERSIONS", "E401", "E403", "EUNSUPPORTEDPROTOCOL", "EBADPLATFORM"}


def _frame(lines, pos):
    for x in lines[pos: pos + 30]:
        m = _STACK.match(x.text)
        if m and "node_modules" not in m.group(1) and "node:internal" not in m.group(1):
            return m
    return None


def parse(lines: list[LogLine], ctx: ParseContext) -> list:
    out = []
    ms = find_first(lines, _MISSING_SCRIPT)
    if ms:
        l, m = ms
        out.append(obs("config", "decisive", NAME, f'npm script "{m.group(1)}" does not exist in package.json', [l], errors=[ExtractedError(trunc(l.text.strip()), l.index + 1, "package.json")], hint="Add the script or fix the workflow command."))
    eng = find_first(lines, _ENGINE)
    if eng:
        out.append(obs("config", "decisive", NAME, "Node version in CI does not satisfy a package engines requirement", [eng[0]], errors=[ExtractedError(trunc(eng[0].text.strip()), eng[0].index + 1)], hint="Bump node-version in setup-node (or .nvmrc)."))
    code = find_first(lines, _NPM_CODE)
    if code and code[1].group(1) != "ELIFECYCLE":
        l, m = code
        c = m.group(1)
        pos = pos_of(lines, l.index)
        detail = [d for d in (_NPM_LINE.match(x.text).group(1) if _NPM_LINE.match(x.text) else "" for x in lines[pos: pos + 25]) if d and not d.startswith(("code ", "A complete log"))][:6]
        hint = {"ERESOLVE": "Peer dependency conflict: align versions in package.json, or use --legacy-peer-deps as a stopgap.",
                "E404": "A package or version in package.json/lockfile does not exist on the registry.", "ETARGET": "A package or version in package.json/lockfile does not exist on the registry.",
                "EINTEGRITY": "Lockfile integrity mismatch: regenerate package-lock.json.", "ENOENT": "A file npm expected is missing (often package.json in the working-directory)."}.get(c, "npm failed; see the npm error lines.")
        out.append(obs("dependency" if c in _DEP_CODES else "config" if c == "ENOENT" else "compile", "decisive" if c in _DEP_CODES else "strong", NAME, f"npm {c}: {trunc(detail[0] if detail else 'install failed', 120)}",
                       [l] + [x for x in lines[pos: pos + 25] if _NPM_LINE.match(x.text)], anchor=l.index, errors=[ExtractedError(trunc(" | ".join(detail)), l.index + 1, "package.json" if c in _DEP_CODES else None, code=c)], signals=[l.text.strip()], hint=hint))
    pn = find_first(lines, _PNPM)
    if pn:
        l, m = pn
        c = m.group(1)
        dep = bool(re.search(r"LOCKFILE|NO_MATCHING_VERSION|FETCH|PEER_DEP|META_FETCH", c))
        out.append(obs("dependency" if dep else "compile", "decisive" if dep else "strong", NAME, f"pnpm ERR_PNPM_{c}", [l], errors=[ExtractedError(trunc(l.text.strip()), l.index + 1, "pnpm-lock.yaml" if "LOCKFILE" in c else None, code=f"ERR_PNPM_{c}")],
                       hint="pnpm-lock.yaml is out of sync with package.json: run `pnpm install` locally and commit the lockfile." if "OUTDATED_LOCKFILE" in c else "pnpm failed; see the error line."))
    cannot = find_first(lines, _CANNOT) or find_first(lines, _WEBPACK) or find_first(lines, _VITE)
    if cannot:
        l, m = cannot
        mod, frm = m.group(1), m.group(2) if m.re is not _CANNOT else None
        fr = _frame(lines, pos_of(lines, l.index))
        rel = mod.startswith((".", "/"))
        file = fr.group(1).replace("file://", "") if fr else frm
        out.append(obs("compile" if rel else "dependency", "strong" if rel else "decisive", NAME, f"Cannot resolve module '{mod}'" + (f" from {frm}" if frm else ""), [l],
                       errors=[ExtractedError(trunc(l.text.strip()), l.index + 1, file, int(fr.group(2)) if fr else None)],
                       hint="A relative import points at a file that does not exist in CI: check the path, case sensitivity, and that the file is committed." if rel else f"'{mod}' is not installed: add it to dependencies (not only devDependencies for production builds) or the lockfile is stale."))
    esm = find_first(lines, _ESM)
    if esm and not cannot:
        out.append(obs("compile", "strong", NAME, f"Node module system error: {esm[1].group(1)}", [esm[0]], errors=[ExtractedError(trunc(esm[0].text.strip()), esm[0].index + 1, code=esm[1].group(1))], hint='ESM/CommonJS interop: check "type" in package.json, file extensions, and the package exports map.'))
    rt = [(l, m) for l, m in find_all(lines, _RUNTIME) if not m.group(0).startswith("Error: Cannot find module")]
    if rt and not cannot:
        l, m = rt[0]
        fr = _frame(lines, pos_of(lines, l.index))
        e = ExtractedError(trunc(f"{m.group(1)}: {m.group(3)}"), l.index + 1, fr.group(1).replace("file://", "") if fr else None, int(fr.group(2)) if fr else None, int(fr.group(3)) if fr else None, m.group(2) or m.group(1))
        out.append(obs("compile" if m.group(1) == "SyntaxError" else "runtime", "strong" if fr else "medium", NAME, f"Node {m.group(1)}: {trunc(m.group(3), 120)}", [l], errors=[e], signals=[l.text.strip()],
                       hint=f"Unhandled {m.group(1)} thrown from {e.file}:{e.line}." if fr else f"Unhandled {m.group(1)}; the stack is in the excerpt."))
    return out
