"""Score a captured RNG sample OR a seed spec against the CryptoHwAuditor rubric.

Stdlib-only. Two modes:

  Sample mode:
    python rng_entropy_score.py --sample sample.hex [--label <name>] [--target-bits N]

    Emits one structured finding with min-entropy, monobit deviation
    (in sigma), longest run of ones, and the repeated-4-byte-block hit
    ratio. Severity per the rubric in SKILL.md. Used to detect the
    Coldcard-class TRNG->PRNG silent-fallback bug.

  Seed mode:
    python rng_entropy_score.py --seed --words N [--passphrase-bits B] [--target-bits N]

    Emits one structured finding for a BIP39 seed spec: raw entropy
    bits + passphrase bits -> effective bits -> pq bits (Grover).
    Severity per the rubric in SKILL.md.

Both modes support ``--json`` (default) and ``--pretty``. The sample
mode never persists or echoes the sample bytes themselves; only their
SHA-256 hash and the derived metrics.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import sys
from collections import Counter
from typing import Any

# BIP39 mnemonic-length -> raw entropy bits (per BIP-0039).
# 12 words -> 128 bits ENT + 4 bits CS = 132 bits; ENT is the security-relevant
# quantity. 24 words -> 256 bits ENT + 8 bits CS.
_BIP39_BITS: dict[int, int] = {
    12: 128,
    15: 160,
    18: 192,
    21: 224,
    24: 256,
}


# ── Sample-mode metrics ────────────────────────────────────────────────

def _min_entropy_bits_per_byte(data: bytes) -> float:
    """Estimate min-entropy H_min per byte from the empirical byte histogram.

    H_min = -log2(max_i p_i). Zero when a single byte value dominates
    the sample (deterministic RNG). 8.0 when every byte value is
    perfectly uniform (ideal RNG).
    """
    if not data:
        return 0.0
    counts = Counter(data)
    p_max = max(counts.values()) / len(data)
    if p_max <= 0.0:
        return 0.0
    return -math.log2(p_max)


def _monobit_sigma(data: bytes) -> float:
    """Signed monobit-test deviation in units of sigma.

    Fair-coin baseline: half the bits should be ones. The count of ones
    is approximately Normal(n/2, sqrt(n)/2). Returns (observed_ones -
    expected_ones) / stddev; positive = more ones than expected.
    """
    n_bits = 8 * len(data)
    if n_bits == 0:
        return 0.0
    ones = sum(bin(b).count("1") for b in data)
    expected = n_bits / 2.0
    stddev = math.sqrt(n_bits) / 2.0
    return (ones - expected) / stddev


def _longest_run(data: bytes) -> int:
    """Longest run of identical bits (either 0s or 1s) in the sample."""
    if not data:
        return 0
    bits = "".join(f"{b:08b}" for b in data)
    longest = current = 1
    for i in range(1, len(bits)):
        if bits[i] == bits[i - 1]:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 1
    return longest


def _repeated_block_ratio(data: bytes, block_size: int = 4) -> float:
    """Fraction of ``block_size`` byte blocks that appear more than once.

    A truly random sample of N blocks over a 2^(8*block_size) space has
    a birthday collision probability of N^2 / 2^(8*block_size + 1); for
    block_size=4 that is negligible below N ~= 65536. Any measurable
    ratio at typical audit sample sizes (a few KB) implies non-random
    structure — the fingerprint of a PRNG cycling on a small state.
    """
    if len(data) < 2 * block_size:
        return 0.0
    n_blocks = len(data) // block_size
    if n_blocks < 2:
        return 0.0
    counts = Counter(
        data[i * block_size : (i + 1) * block_size] for i in range(n_blocks)
    )
    repeated = sum(c for c in counts.values() if c > 1)
    return repeated / n_blocks


def _has_catastrophic_block(data: bytes, block_len: int = 16) -> str | None:
    """Return a description of the first catastrophic run, or None.

    Catastrophic = an all-zero or all-ones run of length >= block_len
    bytes. Either signals a jammed source or a factory-reset default.
    """
    if len(data) < block_len:
        return None
    zeros = b"\x00" * block_len
    ones = b"\xff" * block_len
    if zeros in data:
        return f"all-zero run of >= {block_len} bytes"
    if ones in data:
        return f"all-ones run of >= {block_len} bytes"
    return None


def _sample_severity(
    min_entropy_bpb: float,
    monobit_dev_sigma: float,
    repeated_ratio: float,
    catastrophic: str | None,
) -> tuple[str, str]:
    """Return (severity, one-line finding text). See SKILL.md rubric.

    The repeated-4byte-block ratio is the primary Coldcard-class signal:
    a truly random sample of a few thousand 4-byte blocks has essentially
    zero collisions in a 2^32 space (birthday bound), so anything above
    a few percent is structural. Anything above 25 % — as an LCG with a
    small effective state produces — is catastrophic.
    """
    if catastrophic is not None or min_entropy_bpb < 4.0 or repeated_ratio > 0.25:
        return "HIGH", (
            "RNG output is deterministic or has failed catastrophically "
            f"({catastrophic or f'H_min={min_entropy_bpb:.2f}, '
             f'repeated-block-ratio={repeated_ratio:.4f}'}). "
            "Assume any key derived from this source is recoverable."
        )
    if min_entropy_bpb < 6.0 or repeated_ratio > 0.01:
        return "MEDIUM", (
            "Structural bias present "
            f"(H_min={min_entropy_bpb:.2f}, repeated-block-ratio={repeated_ratio:.4f}). "
            "Likely non-cryptographic RNG. Do not use for key derivation."
        )
    if min_entropy_bpb < 7.0 or abs(monobit_dev_sigma) > 4.0:
        return "LOW", (
            "Detectable bias "
            f"(H_min={min_entropy_bpb:.2f}, monobit={monobit_dev_sigma:.2f} sigma). "
            "Increase sample size and re-audit."
        )
    return "INFO", (
        "RNG output indistinguishable from ideal at this sample size "
        f"(H_min={min_entropy_bpb:.2f}, monobit={monobit_dev_sigma:.2f} sigma)."
    )


def score_sample(data: bytes, label: str = "sample") -> dict[str, Any]:
    """Score a captured RNG sample. See SKILL.md."""
    h = hashlib.sha256(data).hexdigest()
    min_entropy = _min_entropy_bits_per_byte(data)
    monobit = _monobit_sigma(data)
    longest = _longest_run(data)
    repeated = _repeated_block_ratio(data)
    catastrophic = _has_catastrophic_block(data)
    severity, finding = _sample_severity(min_entropy, monobit, repeated, catastrophic)
    return {
        "mode": "sample",
        "label": label,
        "sha256": h,
        "bytes": len(data),
        "min_entropy_bits_per_byte": round(min_entropy, 4),
        "monobit_deviation_sigma": round(monobit, 4),
        "longest_run_bits": longest,
        "repeated_4byte_block_ratio": round(repeated, 6),
        "catastrophic_block": catastrophic,
        "severity": severity,
        "finding": finding,
    }


# ── Seed-mode metrics ──────────────────────────────────────────────────

def _seed_severity(pq_bits: float, target_bits: int) -> tuple[str, str]:
    """Return (severity, one-line finding text). See SKILL.md rubric."""
    if pq_bits >= target_bits:
        return "INFO", f"Meets {target_bits}-bit post-Grover target."
    if pq_bits >= (target_bits * 3) // 4:
        return "LOW", (
            f"Below Cat-1 target ({pq_bits:.0f} < {target_bits} pq-bits). "
            "Add 8-16 passphrase bits on next rotation."
        )
    if pq_bits >= target_bits // 2:
        return "MEDIUM", (
            f"Vulnerable to a CRQC by 2035 ({pq_bits:.0f} pq-bits). "
            "Rotate within 2 years OR add passphrase bits now."
        )
    return "HIGH", (
        f"Recoverable pre-CRQC by nation-state targeting ({pq_bits:.0f} pq-bits). "
        "Rotate immediately AND add >= 32 passphrase bits."
    )


def score_seed(words: int, passphrase_bits: float, target_bits: int = 128) -> dict[str, Any]:
    """Score a BIP39 seed spec. See SKILL.md."""
    if words not in _BIP39_BITS:
        raise ValueError(
            f"words must be one of {sorted(_BIP39_BITS)} (BIP39); got {words}"
        )
    raw = _BIP39_BITS[words]
    effective = raw + max(0.0, passphrase_bits)
    pq_bits = effective / 2.0  # Grover halves brute-force bits.
    severity, finding = _seed_severity(pq_bits, target_bits)
    return {
        "mode": "seed",
        "words": words,
        "raw_entropy_bits": raw,
        "passphrase_bits": passphrase_bits,
        "effective_bits": effective,
        "pq_bits": pq_bits,
        "target_bits": target_bits,
        "severity": severity,
        "finding": finding,
    }


# ── CLI ────────────────────────────────────────────────────────────────

_HEX_RE = re.compile(r"[^0-9a-fA-F]")


def _decode_sample(text: str) -> bytes:
    """Decode either hex (with optional whitespace) or base64."""
    stripped = text.strip()
    if not stripped:
        return b""
    hex_only = _HEX_RE.sub("", stripped)
    if len(hex_only) == len(_HEX_RE.sub("", stripped)) and len(hex_only) % 2 == 0:
        try:
            return bytes.fromhex(hex_only)
        except ValueError:
            pass
    try:
        return base64.b64decode(stripped, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise SystemExit(f"error: could not decode sample as hex or base64: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rng_entropy_score.py",
        description=(
            "Score an RNG sample or a BIP39 seed spec against the "
            "CryptoHwAuditor severity rubric."
        ),
    )
    parser.add_argument("--sample", help="path to hex/base64 RNG sample")
    parser.add_argument("--stdin", action="store_true", help="read sample from stdin")
    parser.add_argument("--label", default="sample", help="human label for the sample")
    parser.add_argument("--seed", action="store_true", help="switch to seed-spec mode")
    parser.add_argument(
        "--words",
        type=int,
        help="BIP39 word count (12, 15, 18, 21, 24). Required with --seed.",
    )
    parser.add_argument(
        "--passphrase-bits",
        type=float,
        default=0.0,
        help="additional entropy bits from a BIP39 passphrase (default 0)",
    )
    parser.add_argument(
        "--target-bits",
        type=int,
        default=128,
        help="post-quantum target bits (default 128, NIST Cat-1)",
    )
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = parser.parse_args(argv)

    if args.seed:
        if args.words is None:
            parser.error("--seed requires --words")
        try:
            finding = score_seed(args.words, args.passphrase_bits, args.target_bits)
        except ValueError as exc:
            parser.error(str(exc))
    elif args.sample or args.stdin:
        if args.stdin:
            text = sys.stdin.read()
        else:
            with open(args.sample, encoding="utf-8") as fh:
                text = fh.read()
        data = _decode_sample(text)
        if not data:
            parser.error("empty sample")
        finding = score_sample(data, label=args.label)
    else:
        parser.error("provide --sample <path>, --stdin, or --seed --words N")

    json.dump(finding, sys.stdout, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
