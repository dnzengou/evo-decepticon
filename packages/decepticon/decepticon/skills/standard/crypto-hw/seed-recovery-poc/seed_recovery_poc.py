"""Owner-consent, ethical PoC for Coldcard-class seed enumerability.

Reads owner-supplied SHA-256 hashes of expected receive-addresses and a
state.json describing the device's known boot state (OSC serial,
SYSTICK range, RTC range). Enumerates the LCG-shaped PRNG state the
Coldcard fallback used; hashes each candidate seed with the SAME
derivation the owner used to produce their hashes; reports
``vulnerable: true`` if any candidate matches.

Stdlib-only. No bitcoin libraries. Simulated derivation
(HMAC-SHA256 with a fixed context string) — this proves enumerability
is feasible without providing a real key-recovery capability.

Refuses if:
  - ``plan/roe.json:machine_enforcement.crypto_hw.owner_consent`` is
    not ``true``, OR
  - ``plan/roe.json:machine_enforcement.crypto_hw.target_class`` is
    not ``"owned_device"``, OR
  - search-cap > 2^28 without ``--acknowledge-cost``.

The recovered candidate is hashed and zeroed in place before the
process returns; the finding never contains the seed value.

Usage
-----
    python seed_recovery_poc.py --hashes hashes.txt --state state.json --pretty
    python seed_recovery_poc.py --hashes h.txt --state s.json \
        --acknowledge-cost --search-cap 268435456   # ~2^28 sweep
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Coldcard LCG constants (glibc-style; publicly known and used by
# rng_entropy_score.py demo scenario A).
_LCG_A = 1103515245
_LCG_C = 12345
_LCG_MOD = 2**31

# Simulated derivation context. NOT bitcoin BIP32/BIP44 — the whole
# point of using a distinct context is to prevent this PoC from being
# usable against real funds. A match here proves state-space size, not
# key recovery.
_DERIV_CTX = b"poc-derivation-v1"

# Hard cap: enumerating 2^32 or more without --acknowledge-cost is
# refused. Even the acknowledged ceiling is 2^32 to prevent DoS.
_DEFAULT_SEARCH_CAP = 1 << 24
_ACKNOWLEDGE_THRESHOLD = 1 << 28
_HARD_CAP = 1 << 32


def _load_roe(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _refuse(reason: str) -> dict[str, Any]:
    return {
        "mode": "seed_recovery_poc",
        "outcome": "blocked",
        "reason": reason,
        "vulnerable": None,
    }


def _lcg_stream(seed: int, n_bytes: int) -> bytes:
    """Produce n_bytes from the low byte of the LCG state stream."""
    state = seed
    out = bytearray()
    while len(out) < n_bytes:
        state = (_LCG_A * state + _LCG_C) % _LCG_MOD
        out.append(state & 0xFF)
    return bytes(out)


def _derive(seed_bytes: bytes) -> bytes:
    """Simulated derivation. NOT bitcoin BIP32/BIP44 — see module docstring."""
    return hmac.new(_DERIV_CTX, seed_bytes, hashlib.sha256).digest()


def _fingerprint_state(state: dict[str, Any]) -> str:
    """SHA-256 of the canonical-JSON of the state parameters."""
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _wipe(b: bytearray) -> None:
    """Best-effort zeroization of a bytearray in place."""
    for i in range(len(b)):
        b[i] = 0


def poc(
    hashes: set[str],
    state: dict[str, Any],
    search_cap: int,
    seed_length: int = 32,
) -> dict[str, Any]:
    """Enumerate LCG-shaped seeds against owner-supplied hashes.

    Returns a finding dict. The recovered candidate (if any) is
    zeroized before returning; only its hash appears in the finding.
    """
    osc = int(state.get("osc", 0))
    systick_range = state.get("systick_range", [0, 0])
    rtc_range = state.get("rtc_range", [0, 0])

    tried = 0
    started = time.monotonic()
    match_hash: str | None = None
    recovered_seed = bytearray(seed_length)

    for rtc in range(int(rtc_range[0]), int(rtc_range[1]) + 1):
        for systick in range(int(systick_range[0]), int(systick_range[1]) + 1):
            # Compose an LCG seed from the three state components. This
            # matches the shape documented in the SKILL.md threat model.
            lcg_seed = (osc ^ (systick << 16) ^ rtc) & (_LCG_MOD - 1)
            candidate = _lcg_stream(lcg_seed, seed_length)
            derived = _derive(candidate)
            derived_hex = hashlib.sha256(derived).hexdigest()
            tried += 1
            if derived_hex in hashes:
                match_hash = derived_hex
                # Copy into the mutable buffer we control so we can wipe it.
                for i in range(seed_length):
                    recovered_seed[i] = candidate[i]
                break
            if tried >= search_cap:
                break
        if match_hash is not None or tried >= search_cap:
            break

    elapsed = time.monotonic() - started

    # Zeroize the recovered candidate before returning. The finding
    # only ever exposes the derived hash, never the seed bytes.
    _wipe(recovered_seed)

    return {
        "mode": "seed_recovery_poc",
        "outcome": "completed",
        "vulnerable": match_hash is not None,
        "candidates_tried": tried,
        "search_cap": search_cap,
        "first_match_derived_hash": match_hash,
        "state_fingerprint_sha256": _fingerprint_state(state),
        "elapsed_seconds": round(elapsed, 4),
        "derivation_context": _DERIV_CTX.decode(),
        "note": (
            "Derivation is HMAC-SHA256 with a fixed context string, NOT "
            "bitcoin BIP32/BIP44. A `vulnerable: true` verdict proves the "
            "state space is small enough to enumerate; it does not recover "
            "real funds. The recovered candidate seed is zeroized before "
            "return; only the derived-hash appears above."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_recovery_poc.py",
        description=(
            "Owner-consent, ethical Coldcard-class seed enumerability PoC. "
            "See SKILL.md for the non-negotiable gates."
        ),
    )
    parser.add_argument(
        "--hashes",
        required=True,
        help="text file of sha256 hex hashes (one per line) of expected receive-address bytes",
    )
    parser.add_argument(
        "--state",
        required=True,
        help="JSON file with {osc:int, systick_range:[lo,hi], rtc_range:[lo,hi]}",
    )
    parser.add_argument("--seed-length", type=int, default=32)
    parser.add_argument(
        "--search-cap",
        type=int,
        default=_DEFAULT_SEARCH_CAP,
        help=f"max candidates to enumerate (default {_DEFAULT_SEARCH_CAP})",
    )
    parser.add_argument(
        "--acknowledge-cost",
        action="store_true",
        help=f"required when --search-cap exceeds {_ACKNOWLEDGE_THRESHOLD}",
    )
    parser.add_argument(
        "--roe-path",
        default="plan/roe.json",
        help="path to plan/roe.json (default: plan/roe.json in cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify gates + parse inputs; do not enumerate",
    )
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = parser.parse_args(argv)

    # ── Gate 1: RoE authorization ────────────────────────────────────
    roe = _load_roe(Path(args.roe_path))
    crypto_hw = (
        roe.get("machine_enforcement", {}).get("crypto_hw", {})
        if isinstance(roe, dict)
        else {}
    )
    owner_consent = crypto_hw.get("owner_consent") is True
    target_class = crypto_hw.get("target_class")
    if not owner_consent or target_class != "owned_device":
        finding = _refuse(
            "plan/roe.json:machine_enforcement.crypto_hw.owner_consent must be "
            "true AND target_class must be 'owned_device'. This skill refuses "
            "on any third-party target."
        )
        json.dump(finding, sys.stdout, indent=2 if args.pretty else None)
        sys.stdout.write("\n")
        return 0  # advisory refuse, not a crash

    # ── Gate 2: search-cap acknowledgement ───────────────────────────
    if args.search_cap > _HARD_CAP:
        parser.error(f"--search-cap must be <= {_HARD_CAP}")
    if args.search_cap > _ACKNOWLEDGE_THRESHOLD and not args.acknowledge_cost:
        finding = _refuse(
            f"--search-cap {args.search_cap} exceeds {_ACKNOWLEDGE_THRESHOLD} "
            "without --acknowledge-cost. Raising the cap costs real wall-clock "
            "time and can be misused as a DoS."
        )
        json.dump(finding, sys.stdout, indent=2 if args.pretty else None)
        sys.stdout.write("\n")
        return 0

    # ── Load inputs ──────────────────────────────────────────────────
    hashes_path = Path(args.hashes)
    state_path = Path(args.state)
    if not hashes_path.is_file():
        parser.error(f"--hashes file not found: {hashes_path}")
    if not state_path.is_file():
        parser.error(f"--state file not found: {state_path}")
    hashes = {
        line.strip().lower()
        for line in hashes_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    if not hashes:
        parser.error("--hashes file is empty")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        parser.error(f"--state is not valid JSON: {exc}")

    # ── Dry-run short-circuit ───────────────────────────────────────
    if args.dry_run:
        finding = {
            "mode": "seed_recovery_poc",
            "outcome": "dry_run",
            "gates_passed": True,
            "hashes_loaded": len(hashes),
            "state_fingerprint_sha256": _fingerprint_state(state),
            "search_cap": args.search_cap,
        }
        json.dump(finding, sys.stdout, indent=2 if args.pretty else None)
        sys.stdout.write("\n")
        return 0

    # ── Enumerate ────────────────────────────────────────────────────
    finding = poc(hashes, state, args.search_cap, args.seed_length)
    json.dump(finding, sys.stdout, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
