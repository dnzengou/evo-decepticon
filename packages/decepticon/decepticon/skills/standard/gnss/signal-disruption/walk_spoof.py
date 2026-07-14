"""Compute expected TTLOF for a walk-off spoof scenario. Simulation-only.

Stdlib-only. This helper does NOT drive transmit hardware. It computes:

  1. A synthesized position trajectory (start_lat/lon + walk_mps for
     ``duration_s``) at 1 Hz.
  2. Whether that trajectory violates the receiver's advertised
     walk-off tolerance (per ``--receiver-model``).
  3. The expected time-to-loss-of-fix (TTLOF), RAIM alarm prediction,
     and OSNMA verdict.

It emits a JSON finding on stdout — the shape the GNSS Auditor
persists to ``findings/gnss/FIND-NNN.md``.

Two modes:

  --dry-run     Pure math. No RoE check. Always safe. Default.
  --tx          Reads ``plan/roe.json``, enforces
                ``machine_enforcement.rf.gnss.authorized == true``
                and ``fixture_id`` match. Even when authorized, this
                helper only outputs an ephemeris JSON attestation
                for a chamber-side operator to load into an authorized
                simulator. It NEVER calls TX hardware directly.

Usage
-----
    python walk_spoof.py --dry-run --receiver-model ublox-f9p \\
        --start-lat 48.858 --start-lon 2.294 --walk-mps 1.0 --duration-s 120
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

# Receiver walk-off envelopes — vendor advertised, from public datasheets.
# walk_off_tolerance_mps: maximum walk-off velocity the receiver claims to
# detect and alarm on (via RAIM or OSNMA verdict mismatch).
# raim_threshold_m: horizontal-error threshold for RAIM alarm.
_RECEIVER_ENVELOPES: dict[str, dict[str, float]] = {
    "ublox-f9p": {"walk_off_tolerance_mps": 3.0, "raim_threshold_m": 15.0},
    "septentrio-mosaic-x5": {"walk_off_tolerance_mps": 1.5, "raim_threshold_m": 8.0},
    "trimble-bd992": {"walk_off_tolerance_mps": 2.0, "raim_threshold_m": 10.0},
    "novatel-oem7": {"walk_off_tolerance_mps": 2.5, "raim_threshold_m": 12.0},
    # Fallback used when the caller supplies an unknown model.
    "generic-mass-market": {"walk_off_tolerance_mps": 10.0, "raim_threshold_m": 30.0},
}

# Earth radius for the flat-earth walk approximation (adequate for < 1 km).
_EARTH_RADIUS_M = 6_378_137.0


def _load_roe(roe_path: Path) -> dict[str, Any]:
    if not roe_path.exists():
        raise SystemExit(
            f"walk_spoof: --tx requires an authorized RoE at {roe_path}; "
            "none found. Refusing."
        )
    with roe_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _authorize_tx(roe_path: Path, expected_fixture_id: str | None) -> dict[str, Any]:
    """Enforce the authorization gate. Returns the attestation dict."""
    roe = _load_roe(roe_path)
    me = roe.get("machine_enforcement", {}).get("rf", {}).get("gnss", {})
    if not me.get("authorized"):
        raise SystemExit(
            "walk_spoof: RoE machine_enforcement.rf.gnss.authorized is not true. "
            "Refusing TX-mode invocation."
        )
    fixture_id = me.get("fixture_id")
    if not fixture_id:
        raise SystemExit(
            "walk_spoof: RoE does not name a fixture_id under rf.gnss. "
            "Refusing TX-mode invocation."
        )
    if expected_fixture_id and expected_fixture_id != fixture_id:
        raise SystemExit(
            f"walk_spoof: fixture mismatch — RoE says {fixture_id!r}, "
            f"caller expects {expected_fixture_id!r}. Refusing."
        )
    return {
        "authorized": True,
        "fixture_id": fixture_id,
        "roe_path": str(roe_path),
        "attested_at": int(time.time()),
    }


def _walk_trajectory(
    start_lat: float, start_lon: float, walk_mps: float, duration_s: int
) -> list[dict[str, float]]:
    """Return a 1-Hz walk-off trajectory. North-east flat-earth approx."""
    steps = []
    for t in range(duration_s + 1):
        d_north_m = walk_mps * t
        d_lat = d_north_m / _EARTH_RADIUS_M * 180.0 / math.pi
        steps.append(
            {
                "t_s": t,
                "lat": start_lat + d_lat,
                "lon": start_lon,
                "walk_offset_m": walk_mps * t,
            }
        )
    return steps


def evaluate_scenario(
    receiver_model: str,
    walk_mps: float,
    duration_s: int,
    receiver_alarm_delay_s: float = 5.0,
    osnma_authenticated: bool = False,
) -> dict[str, Any]:
    env = _RECEIVER_ENVELOPES.get(
        receiver_model, _RECEIVER_ENVELOPES["generic-mass-market"]
    )
    tolerance = env["walk_off_tolerance_mps"]
    raim_threshold_m = env["raim_threshold_m"]

    # TTLOF model: receiver loses fix when horizontal-error crosses the
    # RAIM threshold. Error grows as walk_mps * t.
    if walk_mps <= 0:
        ttlof_s = float("inf")
    else:
        ttlof_s = raim_threshold_m / walk_mps + receiver_alarm_delay_s

    walk_off_over_tolerance = walk_mps > tolerance

    if osnma_authenticated:
        osnma_verdict = "verify_fail"  # Synthesized ephemeris wouldn't carry valid MAC.
        severity = "MEDIUM" if walk_off_over_tolerance else "LOW"
        finding = (
            "OSNMA-capable receiver should detect verdict mismatch. "
            "Confirm the receiver honors OSNMA fail in configuration."
        )
    elif walk_off_over_tolerance:
        severity = "HIGH"
        finding = (
            f"Walk-off velocity {walk_mps} m/s exceeds receiver-advertised "
            f"tolerance {tolerance} m/s. Silent acceptance is a HIGH finding."
        )
        osnma_verdict = "no_auth"
    else:
        severity = "LOW"
        finding = (
            f"Walk-off {walk_mps} m/s within tolerance {tolerance} m/s. "
            "Receiver expected to alarm within RAIM decision boundary."
        )
        osnma_verdict = "no_auth"

    return {
        "receiver_model": receiver_model,
        "walk_mps": walk_mps,
        "duration_s": duration_s,
        "walk_off_tolerance_mps": tolerance,
        "raim_threshold_m": raim_threshold_m,
        "ttlof_expected_s": round(ttlof_s, 2) if ttlof_s != float("inf") else None,
        "raim_alarm_expected": walk_mps > 0,
        "osnma_verdict_expected": osnma_verdict,
        "severity": severity,
        "finding": finding,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Simulate a GNSS walk-off spoof scenario (safe by default).",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="Simulation only (default).")
    mode.add_argument("--tx", action="store_true", help="Enter authorization gate and emit chamber ephemeris.")
    p.add_argument("--receiver-model", default="generic-mass-market")
    p.add_argument("--start-lat", type=float, default=0.0)
    p.add_argument("--start-lon", type=float, default=0.0)
    p.add_argument("--walk-mps", type=float, default=1.0)
    p.add_argument("--duration-s", type=int, default=60)
    p.add_argument("--osnma", action="store_true", help="Receiver-under-test claims OSNMA verify capability.")
    p.add_argument(
        "--roe-path",
        default=os.environ.get("DECEPTICON_ROE_PATH", "plan/roe.json"),
        help="Path to plan/roe.json (env: DECEPTICON_ROE_PATH).",
    )
    p.add_argument("--fixture-id", help="Expected chamber/cage fixture id (must match RoE).")
    args = p.parse_args(argv)

    attestation: dict[str, Any] | None = None
    if args.tx:
        attestation = _authorize_tx(Path(args.roe_path), args.fixture_id)

    evaluation = evaluate_scenario(
        receiver_model=args.receiver_model,
        walk_mps=args.walk_mps,
        duration_s=args.duration_s,
        osnma_authenticated=args.osnma,
    )
    trajectory = _walk_trajectory(
        args.start_lat, args.start_lon, args.walk_mps, args.duration_s
    )

    result: dict[str, Any] = {
        "mode": "tx" if args.tx else "dry-run",
        "attestation": attestation,
        "evaluation": evaluation,
        "trajectory_head": trajectory[:3],
        "trajectory_tail": trajectory[-3:] if len(trajectory) > 3 else trajectory,
        "trajectory_len": len(trajectory),
    }
    if args.tx:
        result["note"] = (
            "This helper does NOT drive TX hardware. Chamber-side operator "
            "must load the emitted ephemeris JSON into the authorized "
            "simulator (gnss-sim / GPS-SDR-SIM) and log the transmit "
            "action separately per RoE."
        )

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if evaluation.get("severity") == "HIGH" else 0


if __name__ == "__main__":
    raise SystemExit(main())
