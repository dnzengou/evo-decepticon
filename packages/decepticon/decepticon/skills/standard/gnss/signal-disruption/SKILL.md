---
name: signal-disruption
description: GNSS receiver resilience testing. Simulates walk-off spoofing and jamming trajectories in a dry-run mode, and — only under written RoE authorization — computes expected TTLOF (time-to-loss-of-fix) inside an authorized chamber/cage. This skill NEVER emits RF outside the authorized fixture. Triggers on 'walk-off', 'spoof', 'jam', 'TTLOF', 'receiver resilience', 'chamber test'.
metadata:
  subdomain: gnss
  when_to_use: "GNSS spoof walk-off jamming receiver resilience TTLOF chamber cage authorized"
  upstream_ref: "ITU-R M.1901, RTCM SC-104, DHS PNT Integrity Library, NIST IR 8323"
---

# GNSS Signal-Disruption Skill

You audit whether a receiver honors its advertised anti-spoofing /
anti-jamming behavior. Every step of this skill has an authorization
gate; the gate opens only when `plan/roe.json` contains a matching
chamber/cage identity AND `machine_enforcement.rf.gnss.authorized == true`.

## ⚠️ Authorization gate — READ FIRST

Before any TX-capable operation, the agent MUST:

1. Read `plan/roe.json`.
2. Assert `machine_enforcement.rf.gnss.authorized === true`.
3. Assert `machine_enforcement.rf.gnss.fixture_id` matches the physical
   fixture reported by the RF chain (chamber ID from spectrum-analyzer
   NDA + cage door interlock, whichever the RoE names).
4. If either check fails, return `outcome=blocked` with the assertion
   that failed and refuse to spawn any process that TX'es.

`walk_spoof.py` performs these checks internally on every invocation.
Do not attempt to disable them.

**Never TX outside the authorized fixture. Never leak I/Q recordings
externally — GNSS multipath + satellite geometry uniquely identifies
the fixture location.**

## Two operating modes

### Mode 1 — `--dry-run` (always safe)

Pure math. Reads receiver spec + walk-off trajectory params, computes
expected TTLOF and walk-off velocity envelope, emits a JSON finding.
No RF, no chamber lookup, no RoE required.

```
python walk_spoof.py --dry-run \
  --receiver-model ublox-f9p \
  --start-lat 48.858 --start-lon 2.294 \
  --walk-mps 1.0 --duration-s 120
```

### Mode 2 — `--tx` (RoE-gated, chamber-only)

Enters the authorization gate above. If gate opens, computes the
same trajectory but ALSO logs a fixture-identity attestation to
`findings/gnss/<utc>-tx/attest.json`. **This helper does not drive
transmit hardware directly** — it emits a synthesized ephemeris JSON
that a chamber-side operator loads into an authorized GPS-SDR-SIM /
gnss-sim rig. The operator's transmit action is out of band and
requires its own operator log entry.

## Findings emitted

For every `--dry-run` or `--tx` invocation, the receiver-under-test
result MUST include:

- `ttlof_expected_s` — computed from receiver-advertised walk-off
  tolerance and the trajectory delta.
- `raim_alarm_expected` — bool, from RAIM decision boundary vs. the
  synthesized ephemeris residuals.
- `osnma_verdict_expected` — `verify_ok` / `verify_fail` / `no_auth`
  based on whether the synthesized ephemeris carries valid OSNMA MAC.
- `severity` — HIGH if the receiver silently accepts a walk-off beyond
  its advertised threshold; MEDIUM if RAIM does not alarm; LOW if the
  receiver drops lock but does not report the anomaly.

## What NOT to do

- Do NOT TX in open air. Ever. GNSS is a protected band (ITU-R RR
  5.208A / 5.328B / 5.443B). Open-air TX is a criminal offense in
  every ITU signatory jurisdiction, RoE or not.
- Do NOT run `walk_spoof.py --tx` on a laptop connected to a
  production network. RF leaks via cable-conducted emission; segregate
  the chamber network per `rf-safety-checklist.md`.
- Do NOT include actual receiver serial numbers or firmware SHAs in
  findings uploaded outside the engagement — treat them as PII of the
  hardware owner.
- Do NOT skip the RAIM second-sensor requirement. A single-antenna
  RAIM alarm is a hypothesis. Cross-check with barometer / INS /
  network-time before promoting to a finding.

## Reference files

- [`rf-safety-checklist.md`](rf-safety-checklist.md) — pre-flight
  checklist. Complete before any `--tx` invocation.
- [`../tesla-pqc-audit/SKILL.md`](../tesla-pqc-audit/SKILL.md) — the
  companion audit skill for the authenticated-tier findings this
  skill's TTLOF measurements depend on.
