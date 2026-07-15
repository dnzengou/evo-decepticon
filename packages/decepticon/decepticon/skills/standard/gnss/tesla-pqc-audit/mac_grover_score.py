"""Score a TESLA/OSNMA/HAS MAC slot against the Grover post-quantum threat.

Stdlib-only. Reads a slot spec (via ``--slot path.json`` or ``--stdin``)
and prints one structured JSON finding to stdout — the shape the
GNSS Auditor persists to ``findings/gnss/FIND-NNN.md``.

Model
-----
For a keyed MAC (HMAC-hash truncated to ``mac_truncation_bits``):

  classical forgery bits = min(hash_output_bits, mac_truncation_bits, key_bits)
  post-quantum forgery bits = classical_forgery_bits // 2  (Grover)

For an unkeyed hash pre-image (rare in TESLA disclosed-key mode, but
present in some legacy chains):

  classical pre-image bits = hash_output_bits
  post-quantum pre-image bits = hash_output_bits // 2

The threshold for a PASS is ``--target-bits`` (default 128, NIST Cat-1).

Usage
-----
    python mac_grover_score.py --slot slot.json
    echo '{"slot_id":"X",...}' | python mac_grover_score.py --stdin
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# Known hash primitives → output bit length. Extend as needed; the
# helper is deterministic and offline.
_HASH_OUTPUT_BITS: dict[str, int] = {
    "SHA-1": 160,
    "SHA-224": 224,
    "SHA-256": 256,
    "SHA-384": 384,
    "SHA-512": 512,
    "SHA3-224": 224,
    "SHA3-256": 256,
    "SHA3-384": 384,
    "SHA3-512": 512,
    "HMAC-SHA-256": 256,
    "HMAC-SHA-384": 384,
    "HMAC-SHA-512": 512,
    "HMAC-SHA3-256": 256,
}


def _severity(pq_bits: int, target_bits: int) -> tuple[str, str]:
    """Return (severity, one-line finding text)."""
    if pq_bits >= target_bits:
        return "INFO", f"Slot meets {target_bits}-bit post-quantum target."
    if pq_bits >= (target_bits * 3) // 4:
        return "LOW", (
            f"Slot below Cat-1 target ({pq_bits} < {target_bits} pq-bits). "
            "Migrate on next spec revision."
        )
    if pq_bits >= target_bits // 2:
        return "MEDIUM", (
            f"Slot vulnerable to CRQC by 2035 ({pq_bits} pq-bits). "
            "Rotate root within 2 years."
        )
    return "HIGH", (
        f"Slot forgeable pre-CRQC ({pq_bits} pq-bits). "
        "Immediate migration required."
    )


def score_slot(slot: dict[str, Any], target_bits: int = 128) -> dict[str, Any]:
    """Compute the finding for one MAC/hash slot."""
    slot_id = str(slot.get("slot_id", "unknown"))
    hash_name = str(slot.get("hash", "")).upper()
    hash_bits = int(slot.get("hash_output_bits") or _HASH_OUTPUT_BITS.get(hash_name, 0))
    if hash_bits <= 0:
        return {
            "slot_id": slot_id,
            "severity": "ERROR",
            "finding": f"Unknown hash primitive: {hash_name!r}. Provide "
            "'hash_output_bits' in the slot spec.",
        }

    mac_bits = slot.get("mac_truncation_bits")
    key_bits = slot.get("key_bits")

    # Keyed MAC forgery — bounded by the smallest of key, tag, hash.
    if mac_bits is not None:
        mac_bits = int(mac_bits)
        classical = min(hash_bits, mac_bits)
        if key_bits is not None:
            classical = min(classical, int(key_bits))
        mode = "MAC-forgery"
    else:
        # Bare hash pre-image mode.
        classical = hash_bits
        mode = "hash-preimage"

    pq_bits = classical // 2
    severity, text = _severity(pq_bits, target_bits)

    recommendation = None
    if severity in ("MEDIUM", "HIGH", "LOW"):
        # Refer buyer to the comparison table; specific target is
        # payload-budget dependent.
        recommendation = (
            "See references/pqc-signature-comparison.md — for MAC "
            "slots, extend truncation to hash_output_bits and lift the "
            "hash to SHA-384 or SHA3-384 (yields 192 pq-bits) if the "
            "SIS payload budget allows."
        )

    return {
        "slot_id": slot_id,
        "hash": hash_name,
        "hash_output_bits": hash_bits,
        "mac_truncation_bits": mac_bits,
        "key_bits": key_bits,
        "mode": mode,
        "classical_forgery_bits": classical,
        "pq_effective_bits": pq_bits,
        "target_pq_bits": target_bits,
        "severity": severity,
        "finding": text,
        "recommendation": recommendation,
        "spec_version": slot.get("spec_version"),
        "context": slot.get("context"),
    }


def _load_slot(path: str | None, from_stdin: bool) -> dict[str, Any]:
    if from_stdin:
        return json.loads(sys.stdin.read())
    if not path:
        raise SystemExit("mac_grover_score: --slot PATH or --stdin required")
    with open(path, encoding="utf-8") as fh:
        return json.loads(fh.read())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Score a TESLA/OSNMA/HAS MAC slot against Grover.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--slot", help="Path to slot spec JSON.")
    src.add_argument("--stdin", action="store_true", help="Read slot JSON from stdin.")
    p.add_argument(
        "--target-bits",
        type=int,
        default=128,
        help="Post-quantum security target (default: 128, NIST Cat-1).",
    )
    args = p.parse_args(argv)

    slot = _load_slot(args.slot, args.stdin)
    finding = score_slot(slot, target_bits=args.target_bits)
    json.dump(finding, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    # Non-zero exit on HIGH so CI / engagement loop can fail-fast.
    return 1 if finding.get("severity") == "HIGH" else 0


if __name__ == "__main__":
    raise SystemExit(main())
