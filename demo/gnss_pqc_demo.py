"""30-second GNSS PQC + resilience commercial demo.

Runs the three GNSS Auditor helpers against a tiny synthetic scenario
and prints a compact operator-facing summary.

Zero external deps; runs in < 1 s. Intended for:

  - Sales-engineering laptop demos (no fixture, no Kali sandbox).
  - CI smoke test that the helpers still import + emit the expected
    finding shapes end-to-end.

Success criteria (asserted below):

  - 3 findings total (2 from tesla-pqc-audit, 1 from signal-disruption).
  - At least 2 findings at ``HIGH`` severity — the P0 buckets the
    commercial deck leads with.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

# Import the three helper mains directly from the skill folders — the
# skills ship as plain .py files alongside SKILL.md, so we add the
# folders to sys.path for the duration of the demo.
_ROOT = Path(__file__).resolve().parents[1]
_GNSS = _ROOT / "packages/decepticon/decepticon/skills/standard/gnss"
sys.path.insert(0, str(_GNSS / "tesla-pqc-audit"))
sys.path.insert(0, str(_GNSS / "signal-disruption"))

import mac_grover_score  # noqa: E402
import root_sig_audit  # noqa: E402
import walk_spoof  # noqa: E402


def _run(main_fn, argv: list[str]) -> dict:
    """Invoke a helper's ``main`` and return the parsed JSON payload."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            main_fn(argv)
    except SystemExit:
        pass
    return json.loads(buf.getvalue())


def _write_slot() -> Path:
    slot = {
        "slot_id": "OSNMA-DEMO-MACK-K1",
        "hash": "HMAC-SHA-256",
        "hash_output_bits": 256,
        "mac_truncation_bits": 40,
        "spec_version": "OSNMA-SIS-ICD-v1.1",
        "context": "Demo slot; matches Galileo E1B disclosed-key MAC truncation.",
    }
    fd, path = tempfile.mkstemp(prefix="gnss-demo-slot-", suffix=".json")
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(slot, f)
    return Path(path)


def _summary_line(finding: dict, idx: int) -> str:
    # tesla-pqc-audit + root_sig_audit put severity + finding at top level.
    # walk_spoof nests them under "evaluation".
    ev = finding.get("evaluation") or finding
    sev = ev.get("severity", "?")
    text = ev.get("finding") or ev.get("rationale") or ""
    return f"[{idx}] {sev} — {text[:180]}"


def main() -> int:
    findings: list[dict] = [
        _run(mac_grover_score.main, ["--slot", str(_write_slot())]),
        _run(root_sig_audit.main, ["--alg", "rsa", "--bits", "2048", "--key-id", "DSM-PKR-DEMO"]),
        _run(
            walk_spoof.main,
            [
                "--dry-run",
                "--receiver-model",
                "generic-mass-market",
                "--walk-mps",
                "15.0",
                "--duration-s",
                "60",
            ],
        ),
    ]

    print("=" * 60)
    print("Decepticon GNSS Auditor — 30-second demo")
    print("=" * 60)
    for i, f in enumerate(findings, 1):
        print(_summary_line(f, i))

    highs = [
        f
        for f in findings
        if (f.get("severity") == "HIGH")
        or (f.get("evaluation", {}).get("severity") == "HIGH")
    ]
    print(f"\nTotal findings: {len(findings)} | HIGH severity: {len(highs)}")
    assert len(findings) == 3, f"expected 3 findings, got {len(findings)}"
    assert len(highs) >= 2, f"expected >=2 HIGH severity, got {len(highs)}"
    print("Demo OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
