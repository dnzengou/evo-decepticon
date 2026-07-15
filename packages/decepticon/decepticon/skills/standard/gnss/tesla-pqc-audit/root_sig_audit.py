"""Audit a DSM-PKR / TESLA root-key signature against Shor + NIST FIPS 204/205.

Stdlib-only. Given a root-signature algorithm, key size, and published
rotation cadence, emits one structured JSON finding: classical bits,
post-quantum bits, severity, and named migration target.

Model
-----
- **RSA / ECDSA**: 0 pq-bits under Shor. Migration is mandatory.
- **ML-DSA (FIPS 204)**: pq-bits per NIST category (Cat 2 = 128, Cat 3 = 192,
  Cat 5 = 256).
- **SLH-DSA (FIPS 205)**: same category mapping, hash-based, larger sigs.
- **Falcon** (draft FIPS 206): Cat 1 = 128 pq-bits (Falcon-512),
  Cat 5 = 256 (Falcon-1024).

Rotation urgency ("migrate by") is derived from
  years_until_crqc − rotation_days / 365
using a fixed CRQC-arrival estimate (2035 = Q(2026)+9y, from the
2024 NIST PQC transition guidance). Callers override with
``--crqc-year``.

Usage
-----
    python root_sig_audit.py --alg rsa --bits 2048 --key-id DSM-PKR-2024
    python root_sig_audit.py --alg ml-dsa-65 --key-id GAL-ROOT-2025
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

_ASSUMED_CRQC_YEAR = 2035
_AUDIT_YEAR = 2026

# (algorithm, key_bits) → (classical_bits, pq_bits). key_bits is the
# nominal key strength — RSA modulus size or curve size in bits. For
# PQC families we key on the parameter set name and ignore key_bits.
_CLASSICAL_TABLE: dict[str, dict[int, int]] = {
    "rsa": {1024: 80, 2048: 112, 3072: 128, 4096: 152, 7680: 192, 15360: 256},
    "ecdsa": {192: 96, 224: 112, 256: 128, 384: 192, 521: 256},
}

_PQC_TABLE: dict[str, tuple[int, str]] = {
    # (pq_bits, nist_category)
    "ml-dsa-44": (128, "Cat 2"),
    "ml-dsa-65": (192, "Cat 3"),
    "ml-dsa-87": (256, "Cat 5"),
    "slh-dsa-128s": (128, "Cat 1"),
    "slh-dsa-128f": (128, "Cat 1"),
    "slh-dsa-192s": (192, "Cat 3"),
    "slh-dsa-192f": (192, "Cat 3"),
    "slh-dsa-256s": (256, "Cat 5"),
    "slh-dsa-256f": (256, "Cat 5"),
    "falcon-512": (128, "Cat 1"),
    "falcon-1024": (256, "Cat 5"),
}


def _classical_bits(alg: str, bits: int | None) -> int:
    table = _CLASSICAL_TABLE.get(alg.lower())
    if not table or bits is None:
        return 0
    # Round down to nearest known size — RSA-2050 is treated as RSA-2048.
    known = sorted(k for k in table if k <= bits)
    if not known:
        return 0
    return table[known[-1]]


def _recommend_migration(payload_page_bytes: int | None) -> dict[str, Any]:
    """Return the recommended PQC target, keyed on SIS-ICD payload budget."""
    if payload_page_bytes is None or payload_page_bytes >= 4096:
        return {
            "target": "ML-DSA-65",
            "pq_bits": 192,
            "sig_bytes": 3293,
            "pk_bytes": 1952,
            "rationale": "Cat-3, 192 pq-bits, fits typical SIS-ICD page budgets ≥ 4 KB.",
        }
    if payload_page_bytes >= 900:
        return {
            "target": "Falcon-512",
            "pq_bits": 128,
            "sig_bytes": 666,
            "pk_bytes": 897,
            "rationale": "Cat-1, 128 pq-bits, smallest PQC sig fitting sub-1 KB pages.",
        }
    return {
        "target": "aggregated-ML-DSA-44",
        "pq_bits": 128,
        "sig_bytes": 2420,
        "pk_bytes": 1312,
        "rationale": (
            "Sub-900-byte pages force multi-frame signature aggregation. "
            "Consider a hybrid transitional mode with per-frame HMAC and "
            "cross-frame ML-DSA-44 root."
        ),
    }


def _rotation_verdict(
    rotation_days: int | None,
    classical_bits: int,
    pq_bits: int,
    crqc_year: int,
    audit_year: int,
) -> dict[str, Any]:
    years_until_crqc = max(0, crqc_year - audit_year)
    if pq_bits >= 128:
        return {
            "urgency": "none",
            "years_slack": years_until_crqc,
            "notes": "PQC-adequate — rotate on normal cadence.",
        }
    if rotation_days is None:
        return {
            "urgency": "unknown",
            "years_slack": None,
            "notes": "Rotation cadence not provided.",
        }
    residual_key_lifetime_years = rotation_days / 365.0
    if residual_key_lifetime_years > years_until_crqc:
        return {
            "urgency": "immediate",
            "years_slack": round(years_until_crqc - residual_key_lifetime_years, 2),
            "notes": (
                f"Current root will outlive CRQC arrival (~{crqc_year}) — "
                f"key lifetime {residual_key_lifetime_years:.1f} y > "
                f"{years_until_crqc} y remaining. Rotate to PQC now."
            ),
        }
    return {
        "urgency": "planned",
        "years_slack": round(years_until_crqc - residual_key_lifetime_years, 2),
        "notes": (
            f"Rotation ({residual_key_lifetime_years:.1f} y) precedes CRQC "
            f"({years_until_crqc} y). Plan PQC migration before next cycle."
        ),
    }


def audit_root(
    alg: str,
    bits: int | None = None,
    key_id: str | None = None,
    rotation_days: int | None = None,
    payload_page_bytes: int | None = None,
    crqc_year: int = _ASSUMED_CRQC_YEAR,
    audit_year: int = _AUDIT_YEAR,
) -> dict[str, Any]:
    alg_l = alg.lower()
    if alg_l in _PQC_TABLE:
        pq_bits, cat = _PQC_TABLE[alg_l]
        classical = pq_bits  # PQC parameters give classical ≥ pq_bits by construction.
        severity = "INFO" if pq_bits >= 128 else "LOW"
        finding = (
            f"Root is PQC-native ({alg_l}, NIST {cat}, {pq_bits} pq-bits)."
            if pq_bits >= 128
            else f"Root is PQC but below Cat-1 target ({pq_bits} < 128 pq-bits)."
        )
        migration = None
    elif alg_l in _CLASSICAL_TABLE:
        classical = _classical_bits(alg_l, bits)
        pq_bits = 0
        severity = "HIGH"
        finding = (
            f"Root is Shor-vulnerable ({alg_l.upper()}-{bits}). "
            f"0 pq-bits under a CRQC. Classical strength ~{classical} bits."
        )
        migration = _recommend_migration(payload_page_bytes)
    else:
        return {
            "key_id": key_id,
            "alg": alg,
            "severity": "ERROR",
            "finding": (
                f"Unknown signature algorithm: {alg!r}. Supported: "
                + ", ".join(sorted(list(_PQC_TABLE) + list(_CLASSICAL_TABLE)))
            ),
        }

    rotation = _rotation_verdict(rotation_days, classical, pq_bits, crqc_year, audit_year)

    return {
        "key_id": key_id,
        "alg": alg_l,
        "key_bits": bits,
        "classical_bits": classical,
        "pq_bits": pq_bits,
        "severity": severity,
        "finding": finding,
        "rotation": rotation,
        "migration": migration,
        "crqc_year": crqc_year,
        "audit_year": audit_year,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Audit a DSM-PKR root signature for post-quantum readiness.",
    )
    p.add_argument("--alg", required=True, help="Signature algorithm (rsa, ecdsa, ml-dsa-65, ...).")
    p.add_argument("--bits", type=int, help="Key size in bits (RSA modulus / ECDSA curve).")
    p.add_argument("--key-id", help="Human-readable key identifier for the finding.")
    p.add_argument(
        "--rotation-days",
        type=int,
        help="Published key-rotation cadence in days (from spec or operator).",
    )
    p.add_argument(
        "--payload-page-bytes",
        type=int,
        help="SIS-ICD signed-page payload budget, used to pick migration target.",
    )
    p.add_argument(
        "--crqc-year",
        type=int,
        default=_ASSUMED_CRQC_YEAR,
        help=f"Assumed CRQC-arrival year (default: {_ASSUMED_CRQC_YEAR}).",
    )
    args = p.parse_args(argv)

    finding = audit_root(
        alg=args.alg,
        bits=args.bits,
        key_id=args.key_id,
        rotation_days=args.rotation_days,
        payload_page_bytes=args.payload_page_bytes,
        crqc_year=args.crqc_year,
    )
    json.dump(finding, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if finding.get("severity") == "HIGH" else 0


if __name__ == "__main__":
    raise SystemExit(main())
