"""30-second Crypto HW Auditor commercial demo (Coldcard-style).

Runs the ``rng_entropy_score`` helper against three synthetic scenarios
that mirror the shape of the 2026 Coldcard incident, and prints a
compact operator-facing summary.

Zero external deps; runs in < 1 s. Intended for:

  - Sales-engineering laptop demos (no hardware, no captured sample).
  - CI smoke test that the helper still emits the expected finding
    shape end-to-end.
  - Copy-paste evidence that the Crypto HW Auditor CAN respond to a
    Coldcard-class TRNG-to-PRNG regression the way a Red-Team member
    would (score, verdict, remediation ladder).

Success criteria (asserted below):

  - 3 findings total.
  - Scenario A (Coldcard-shaped weak PRNG) is HIGH severity.
  - Scenario B (healthy TRNG) is INFO severity.
  - Scenario C (24-word seed + 32 passphrase bits) is INFO severity;
    Scenario C' (24-word seed, no passphrase) is LOW-or-worse — the
    demo asserts the delta so a regression in the seed helper is
    caught by CI.
"""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

# Import the helper directly from the skill folder — the skills ship as
# plain .py files alongside SKILL.md, so we add the folder to sys.path
# for the duration of the demo.
_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _ROOT / "packages/decepticon/decepticon/skills/standard/crypto-hw/rng-entropy-audit"
sys.path.insert(0, str(_SKILL))

import rng_entropy_score  # noqa: E402


def _run(argv: list[str], *, stdin: str | None = None) -> dict:
    """Invoke the helper's main and return the parsed JSON payload."""
    buf = io.StringIO()
    old_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(buf):
            rng_entropy_score.main(argv)
    except SystemExit:
        pass
    finally:
        sys.stdin = old_stdin
    return json.loads(buf.getvalue())


# ── Scenario builders ─────────────────────────────────────────────────

def _coldcard_shaped_prng_sample() -> str:
    """16 KiB sample from a small-state LCG mimicking the Coldcard regression.

    A Linear Congruential Generator seeded from a plausible stable
    device value (serial + boot counter). The output is fully
    deterministic and cycles quickly — the same fingerprint you would
    see if a hardware TRNG silently fell back to a software PRNG
    seeded from device state. The low byte of an LCG has good per-byte
    uniformity but the 4-byte-block space collapses onto a small cycle,
    which is exactly what the repeated-block-ratio metric catches.
    """
    seed = 0x0BAD_C0DE_C01D_CA1D  # "bad code, cold card" — clearly not TRNG.
    state = seed
    modulus = 2**31
    a = 1103515245
    c = 12345
    out = bytearray()
    while len(out) < 16 * 1024:
        state = (a * state + c) % modulus
        out.append(state & 0xFF)
    return out.hex()


def _healthy_trng_sample() -> str:
    """16 KiB sample from ``os.urandom`` — a healthy hardware-backed RNG.

    At 16 KiB the max-bucket variance settles enough that H_min lands
    comfortably above the 7.0 INFO threshold for the vast majority of
    draws (a smaller 4 KiB sample can dip below 7.0 due to natural
    Poisson-shaped bucket-count spread, producing a spurious LOW).
    """
    return os.urandom(16 * 1024).hex()


def _summary_line(finding: dict, idx: int, label: str) -> str:
    sev = finding.get("severity", "?")
    text = finding.get("finding", "")
    return f"[{idx}] {sev} - {label}: {text[:180]}"


def main() -> int:
    # Scenario A — Coldcard-shaped TRNG->PRNG silent fallback.
    weak_hex = _coldcard_shaped_prng_sample()
    scenario_a = _run(["--stdin", "--label", "coldcard-mk3-post-2021"], stdin=weak_hex)

    # Scenario B — healthy TRNG. Expected verdict: INFO.
    healthy_hex = _healthy_trng_sample()
    scenario_b = _run(["--stdin", "--label", "reference-trng"], stdin=healthy_hex)

    # Scenario C — 24-word seed + 32 passphrase bits. Expected: INFO.
    scenario_c = _run(["--seed", "--words", "24", "--passphrase-bits", "32"])

    # Scenario C' — same seed, no passphrase. Used in the delta assertion
    # so a regression in the seed helper is caught. Not printed as its
    # own line; folded into the C row.
    scenario_c_bare = _run(["--seed", "--words", "24", "--passphrase-bits", "0"])

    findings = [
        (scenario_a, "Coldcard-shaped RNG (weak PRNG)"),
        (scenario_b, "reference TRNG (os.urandom)"),
        (scenario_c, "24-word seed + 32-bit passphrase"),
    ]

    print("=" * 66)
    print("Decepticon Crypto HW Auditor - 30-second demo")
    print("=" * 66)
    for i, (f, label) in enumerate(findings, 1):
        print(_summary_line(f, i, label))
    print()
    print(
        "Seed delta:  24-word bare = "
        f"{scenario_c_bare['pq_bits']:.0f} pq-bits ({scenario_c_bare['severity']})  "
        f"->  + 32 passphrase = {scenario_c['pq_bits']:.0f} pq-bits ({scenario_c['severity']})"
    )

    highs = [f for f, _ in findings if f.get("severity") == "HIGH"]
    infos = [f for f, _ in findings if f.get("severity") == "INFO"]
    print(f"\nTotal findings: {len(findings)} | HIGH: {len(highs)} | INFO: {len(infos)}")

    assert len(findings) == 3, f"expected 3 findings, got {len(findings)}"
    assert scenario_a["severity"] == "HIGH", (
        f"Coldcard-shaped PRNG must be HIGH, got {scenario_a['severity']}"
    )
    assert scenario_b["severity"] == "INFO", (
        f"healthy TRNG must be INFO, got {scenario_b['severity']}"
    )
    assert scenario_c["severity"] == "INFO", (
        f"24-word + 32 passphrase must be INFO, got {scenario_c['severity']}"
    )
    assert scenario_c["pq_bits"] > scenario_c_bare["pq_bits"], (
        "passphrase bits must strictly improve pq_bits"
    )
    print("Demo OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
