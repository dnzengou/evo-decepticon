---
name: tesla-pqc-audit
description: GNSS signal-in-space authentication PQC audit. Scores TESLA/OSNMA MAC + tag lengths against Grover's post-quantum threat, audits DSM-PKR root signatures for Shor-vulnerability, and produces migration findings targeting ML-DSA / SLH-DSA / Falcon. Triggers on 'TESLA', 'OSNMA', 'Galileo HAS', 'PQC audit', 'MAC length', 'root key', 'DSM-PKR'.
metadata:
  subdomain: gnss
  when_to_use: "TESLA MAC audit OSNMA tag length HAS PQC signature root key DSM-PKR Grover Shor post-quantum GNSS authentication"
  upstream_ref: "Galileo OSNMA SIS-ICD v1.1, Galileo HAS SIS-ICD Issue 1.0, NIST FIPS 204/205, RFC 4082 (TESLA)"
---

# TESLA / OSNMA / HAS PQC Audit Skill

You are auditing whether a GNSS space-segment authentication stack still
provides ≥128-bit security under the 2026 quantum threat model. The
inputs are auth-slot specs extracted from I/Q captures or SIS-ICDs; the
outputs are structured findings that either (a) certify the slot as
PQC-adequate or (b) name the exact migration path.

## Threat model (fixed — do not renegotiate)

- **Grover** halves the effective pre-image / MAC-forgery bits of every
  hash primitive. A 256-bit hash gives 128 pq-bits of pre-image
  resistance; a 128-bit MAC truncation gives 64 pq-bits of forgery
  resistance — sub-target.
- **Shor** breaks RSA and ECDSA in polynomial time on a CRQC. Any root
  signature over RSA-* or ECDSA-* is 0-bit under this model.
- **Migration targets** (NIST FIPS 204/205, October 2025):
  - ML-DSA-65 (Cat 3, 192-bit pq) for root signatures where sig size ≤
    3.3 KB fits the SIS-ICD page budget.
  - SLH-DSA-192s where you can tolerate 16 KB sigs but need
    hash-based conservatism.
  - Falcon-512 (Cat 1, 128-bit pq) where SIS bandwidth is the binding
    constraint (~666-byte sig, ~897-byte pk).

## Operating principles

1. **Score before you speculate.** Run `mac_grover_score.py` on every
   TESLA / OSNMA / HAS MAC slot and `root_sig_audit.py` on every DSM-PKR
   root key. Findings ship with structured severity — no free-form
   speculation.
2. **Never touch the RF.** This skill is record-only. Any TX-capable
   action lives in [`signal-disruption`](../signal-disruption/SKILL.md)
   and is gated on `plan/roe.json`.
3. **Cross-reference spec versions.** OSNMA v1 vs v0 changes the
   effective tag length; HAS auth is TX-scheduled per ICD Issue.
   Findings without a spec-version anchor are advisory only.
4. **Cite migration cost.** A finding that says "migrate to ML-DSA-65"
   without a sig-size vs. SIS-page-budget note is not actionable.
   Reference [`references/pqc-signature-comparison.md`](references/pqc-signature-comparison.md).

## Severity rubric

| pq effective bits | Severity | Finding |
|-------------------|----------|---------|
| ≥ 128             | INFO     | Slot meets NIST Cat-1 pq target. |
| 96 – 127          | LOW      | Below Cat-1. Migrate on next spec revision. |
| 64 – 95           | MEDIUM   | Vulnerable to CRQC by 2035. Rotate root within 2 years. |
| < 64              | HIGH     | Forgeable pre-CRQC by targeted collision. Immediate. |
| RSA/ECDSA root    | HIGH     | 0-bit pq. Root-of-trust must migrate to ML-DSA / SLH-DSA. |

## Workflow

```
1. Load captures + spec version from findings/gnss/<utc>-capture/.
2. For each MAC slot:
     python mac_grover_score.py --slot <slot.json>
   Emit finding = { slot_id, hash, mac_bits, pq_bits, severity, rec }.
3. For each root signature:
     python root_sig_audit.py --alg <alg> --bits <n> --rotation-days <n>
   Emit finding = { key_id, alg, classical_bits, pq_bits, severity, migration }.
4. Cross-check every HIGH finding against references/pqc-signature-comparison.md
   to confirm the recommended migration fits the SIS-ICD payload budget.
5. Persist findings to findings/gnss/FIND-NNN.md via the shared
   finding-protocol skill; each MUST cite the spec version audited.
```

## Inputs

**MAC slot JSON** (feed to `mac_grover_score.py`):

```json
{
  "slot_id": "OSNMA-MACSEQ-2024-K3",
  "hash": "HMAC-SHA-256",
  "hash_output_bits": 256,
  "mac_truncation_bits": 40,
  "spec_version": "OSNMA-SIS-ICD-v1.1",
  "context": "Galileo E1B TESLA disclosed-key MAC"
}
```

**Root-signature spec** (feed to `root_sig_audit.py`):

```
--alg rsa --bits 2048 --key-id DSM-PKR-2024 --rotation-days 730
```

## What NOT to do

- Do NOT run `mac_grover_score.py` on encryption primitives — it models
  MAC forgery, not key recovery. Symmetric encryption uses AES; Grover
  gives 128 pq-bits on AES-256 which is separately fine.
- Do NOT emit HIGH on a truncated MAC without checking whether the
  truncation is over a keyed HMAC (forgery limited by key size, not
  hash size). The helper handles this; hand-computing severity risks
  double-counting the Grover factor.
- Do NOT recommend RSA-4096 as a "temporary hardening". Under Shor it
  is 0-bit like RSA-2048; the migration is to a PQC family, not a
  larger classical key.
- Do NOT load `signal-disruption`. That skill involves TX and is gated
  on `plan/roe.json:machine_enforcement.rf.gnss.authorized`.
