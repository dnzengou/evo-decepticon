"""Static audit for the Coldcard-class defined-but-zero guard-macro firmware bug.

Stdlib-only. Scans a firmware source tree for the three signals
documented in SKILL.md:

  A. `#ifndef X` guards protecting a hardware-RNG init call while
     `X` is defined elsewhere as `0` (guard silently disables the
     feature it claims to protect).
  B. Same-name RNG symbol exported by both a hw/ and a soft/ or
     fallback/ path — linker picks the fallback silently.
  C. Weak-symbol RNG functions (`__attribute__((weak))`) which
     an aliased fallback can override without any linker warning.

Emits one structured JSON finding to stdout (finding shape mirrors
rng_entropy_score.py).

Usage
-----
    python firmware_guard_audit.py --src /path/to/firmware [--pretty]
    python firmware_guard_audit.py --src /path/to/firmware --firmware-version 4.2.0

The optional --firmware-version + --vendor Coldcard combination
cross-checks against the published vulnerable range and auto-escalates
severity to HIGH when the firmware is in-range even if no code match
fires (because the bug can hide in a build-system path this scanner
does not model).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ── Static patterns ────────────────────────────────────────────────────

# Config-file lines that DEFINE a macro to zero (or false). Captured
# name is used to look for a corresponding #ifndef guard.
_DEFINE_ZERO_RE = re.compile(
    r"^\s*#\s*define\s+(\w+)\s*\(?\s*(?:0|false|FALSE)\s*\)?\s*(?://|/\*|$)",
    re.MULTILINE,
)

# Guard forms that fire on presence-of-macro, not truth-of-macro.
_GUARD_IFNDEF_RE = re.compile(r"^\s*#\s*ifndef\s+(\w+)", re.MULTILINE)
_GUARD_IFDEF_INVERSE_RE = re.compile(
    r"^\s*#\s*ifdef\s+(\w+)\s*\r?\n[\s\S]*?^\s*#\s*else",
    re.MULTILINE,
)

# Weak-symbol RNG functions.
_WEAK_SYMBOL_RE = re.compile(
    r"__attribute__\s*\(\s*\(\s*weak\s*\)\s*\)[^;]*?\b(\w*rng\w*|\w*random\w*|\w*entropy\w*)\s*\(",
    re.IGNORECASE,
)

# RNG-shaped function definitions — used to detect same-name aliasing.
_RNG_FN_DEF_RE = re.compile(
    r"^\s*(?:uint8_t|uint16_t|uint32_t|int|void|static|extern)\s+"
    r"(\w*(?:rng|random|entropy)\w*)\s*\(",
    re.MULTILINE | re.IGNORECASE,
)


# Vendor known-vulnerable firmware ranges. Extend as advisories publish.
_KNOWN_VULN: dict[str, list[tuple[str, str, str]]] = {
    # Coldcard 2026-07-05 postmortem. Ranges are inclusive.
    "coldcard": [
        # (product line, from-version, to-version). The "to" version is
        # the last vulnerable release; the fix ships in the next one.
        ("mk2", "4.1.9", "4.4.1"),
        ("mk3", "4.1.9", "4.4.1"),
        ("mk4", "0.0.0", "5.3.99"),  # fixed in 5.4.0
        ("q",   "0.0.0", "5.3.99"),  # fixed in 5.4.0
        ("edge","0.0.0", "6.6.65"),  # fixed in 6.6.66
    ],
}


# ── Version compare (dotted decimals, tolerant of extra segments) ──────

def _version_tuple(v: str) -> tuple[int, ...]:
    parts = re.split(r"[.\-+]", v.strip().lstrip("v"))
    out: list[int] = []
    for p in parts:
        m = re.match(r"^\d+", p)
        out.append(int(m.group(0)) if m else 0)
    return tuple(out)


def _version_in_range(v: str, lo: str, hi: str) -> bool:
    return _version_tuple(lo) <= _version_tuple(v) <= _version_tuple(hi)


# ── Scanners ───────────────────────────────────────────────────────────

def _iter_source_files(root: Path):
    """Yield C/C++/header files under ``root``."""
    exts = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".inc"}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            # Skip common vendor/build dirs to keep the scan honest.
            skip = {"build", "cmake-build", "third_party", ".git", "node_modules"}
            if any(part in skip for part in p.parts):
                continue
            yield p


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _line_of_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _find_defined_zero_macros(files: list[tuple[Path, str]]) -> dict[str, list[tuple[Path, int]]]:
    """Return {macro_name: [(file, line), ...]} for `#define X 0` lines."""
    hits: dict[str, list[tuple[Path, int]]] = {}
    for path, text in files:
        for m in _DEFINE_ZERO_RE.finditer(text):
            name = m.group(1)
            hits.setdefault(name, []).append((path, _line_of_offset(text, m.start())))
    return hits


def _find_ifndef_guards(
    files: list[tuple[Path, str]],
    defined_zero: dict[str, list[tuple[Path, int]]],
) -> list[dict[str, Any]]:
    """Return findings for `#ifndef X` guards where X is defined-but-zero elsewhere."""
    findings: list[dict[str, Any]] = []
    for path, text in files:
        for m in _GUARD_IFNDEF_RE.finditer(text):
            name = m.group(1)
            if name in defined_zero:
                findings.append({
                    "rule": "A_defined_but_zero_guard",
                    "macro": name,
                    "guard_file": str(path),
                    "guard_line": _line_of_offset(text, m.start()),
                    "defined_zero_at": [
                        {"file": str(p), "line": ln}
                        for p, ln in defined_zero[name]
                    ],
                })
    return findings


def _find_same_name_rng(files: list[tuple[Path, str]]) -> list[dict[str, Any]]:
    """Return findings for the same RNG symbol defined in >= 2 files."""
    fn_defs: dict[str, list[tuple[Path, int]]] = {}
    for path, text in files:
        for m in _RNG_FN_DEF_RE.finditer(text):
            name = m.group(1)
            fn_defs.setdefault(name, []).append((path, _line_of_offset(text, m.start())))
    findings: list[dict[str, Any]] = []
    for name, locs in fn_defs.items():
        distinct_files = {p for p, _ in locs}
        if len(distinct_files) < 2:
            continue
        # The bug signature is BOTH a hw path AND a soft/fallback path.
        parts = [str(p).replace("\\", "/").lower() for p in distinct_files]
        hw_hit = any(re.search(r"/(hw|hal|driver|drivers|chip)/", s) for s in parts)
        soft_hit = any(re.search(r"/(soft|fallback|port|sim|mock|micropython)/", s) for s in parts)
        if hw_hit and soft_hit:
            findings.append({
                "rule": "B_same_name_hw_vs_soft",
                "symbol": name,
                "locations": [{"file": str(p), "line": ln} for p, ln in locs],
            })
    return findings


def _find_weak_symbols(files: list[tuple[Path, str]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path, text in files:
        for m in _WEAK_SYMBOL_RE.finditer(text):
            findings.append({
                "rule": "C_weak_symbol_rng",
                "symbol": m.group(1),
                "file": str(path),
                "line": _line_of_offset(text, m.start()),
            })
    return findings


# ── Severity ───────────────────────────────────────────────────────────

def _severity(matches: list[dict[str, Any]], known_vulnerable: bool) -> tuple[str, str]:
    if known_vulnerable:
        return "HIGH", (
            "Firmware version is in a published vulnerable range. "
            "Rotate any seed generated on this firmware. Findings below "
            "may or may not reproduce the exact vendor bug path — the "
            "range check alone is authoritative."
        )
    if not matches:
        return "INFO", "No defined-but-zero guards or same-name RNG symbols matched."
    rules = {m["rule"][0] for m in matches}  # A / B / C
    if "A" in rules or "B" in rules:
        return "HIGH", (
            f"Coldcard-class pattern matched ({len(matches)} finding(s)). "
            "Any of rules A/B silently disables hardware RNG at build time; "
            "rebuild after fixing the guard or the linker precedence."
        )
    return "MEDIUM", (
        f"Weak-symbol RNG function found ({len(matches)} finding(s)). "
        "Not necessarily exploitable, but any downstream object that "
        "defines the same symbol overrides this one without a linker warning."
    )


# ── Main ──────────────────────────────────────────────────────────────

def audit(src: Path, vendor: str | None, firmware_version: str | None) -> dict[str, Any]:
    files = [(p, _read(p)) for p in _iter_source_files(src)]
    defined_zero = _find_defined_zero_macros(files)
    findings_a = _find_ifndef_guards(files, defined_zero)
    findings_b = _find_same_name_rng(files)
    findings_c = _find_weak_symbols(files)
    matches = findings_a + findings_b + findings_c

    known_vulnerable = False
    known_vuln_evidence: list[dict[str, str]] = []
    if vendor and firmware_version:
        vendor_key = vendor.strip().lower()
        for line, lo, hi in _KNOWN_VULN.get(vendor_key, []):
            if _version_in_range(firmware_version, lo, hi):
                known_vulnerable = True
                known_vuln_evidence.append(
                    {"product": line, "from": lo, "to": hi}
                )

    severity, finding_text = _severity(matches, known_vulnerable)

    return {
        "mode": "firmware_guard_audit",
        "src": str(src),
        "vendor": vendor,
        "firmware_version": firmware_version,
        "files_scanned": len(files),
        "known_vulnerable": known_vulnerable,
        "known_vulnerable_evidence": known_vuln_evidence,
        "matches": matches,
        "severity": severity,
        "finding": finding_text,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="firmware_guard_audit.py",
        description=(
            "Static audit for the Coldcard-class defined-but-zero guard-macro "
            "firmware bug (see SKILL.md)."
        ),
    )
    parser.add_argument("--src", required=True, help="firmware source tree root")
    parser.add_argument("--vendor", help="vendor slug (e.g. coldcard)")
    parser.add_argument("--firmware-version", help="firmware version (e.g. 4.2.0)")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = parser.parse_args(argv)

    src = Path(args.src)
    if not src.is_dir():
        parser.error(f"--src is not a directory: {src}")

    finding = audit(src, args.vendor, args.firmware_version)
    json.dump(finding, sys.stdout, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
