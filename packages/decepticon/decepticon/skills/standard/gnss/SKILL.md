---
name: gnss-overview
description: >
  Top-level index for the Decepticon GNSS / PNT audit suite. Routes the
  GnssAuditor to the correct leaf skill based on target (signal-in-space
  authentication vs receiver resilience). Physical-layer TX is gated
  behind ``plan/roe.json:machine_enforcement.rf.gnss`` and defaults to
  record-and-review.
allowed-tools: Bash Read Write
metadata:
  subdomain: gnss
  when_to_use: "GNSS, GPS, Galileo, OSNMA, TESLA, HAS, DSM-PKR, PQC, PNT, spoofing, jamming, TTLOF, walk-off, RAIM, receiver audit"
  tags:
    - gnss
    - pnt
    - osnma
    - tesla
    - has
    - pqc
    - spoofing
    - jamming
  mitre_attack: T1497, T1499
---

# GNSS / PNT Audit Suite — Auditor Index

> Load the auditor workflow first each iteration (RoE authorization
> check, phase progression, KG node contract). This file is the routing
> layer on top of that workflow.

## Playbook table

| Leaf skill | Trigger | Primary MITRE | Status |
|---|---|---|---|
| [tesla-pqc-audit](tesla-pqc-audit/SKILL.md) | TESLA / OSNMA / HAS auth stack in scope | T1497 | shipped |
| [signal-disruption](signal-disruption/SKILL.md) | Receiver resilience under jamming / spoofing | T1499 | shipped |

## Selection tree

```
if scope names TESLA / OSNMA / HAS / DSM-PKR / PQC     → tesla-pqc-audit
if scope names receiver / TTLOF / walk-off / spoof     → signal-disruption
if both, run tesla-pqc-audit first (record-only, safe),
then signal-disruption under RoE authorization
```

## Authorization gate (both skills)

```
authorized = plan/roe.json:machine_enforcement.rf.gnss.authorized
fixture    = plan/roe.json:machine_enforcement.rf.gnss.fixture

  authorized == false OR missing  → refuse TX; record-only permitted
  authorized == true              → verify fixture matches physical
                                     chamber/cage identity before TX
```

## KG node contract

Both skills write the same node types:

| Node kind | Typical props |
|---|---|
| `Receiver` | model, firmware, constellations, auth_verify |
| `Signal`   | constellation, band, prn, auth_layer |
| `Finding`  | title, severity, cvss, cwe, remediation |
| `Fixture`  | id, kind (chamber/cage), cal_date |
