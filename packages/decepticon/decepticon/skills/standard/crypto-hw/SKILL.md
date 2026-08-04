---
name: crypto-hw-overview
description: >
  Top-level index for the Decepticon cryptographic-hardware audit suite.
  Routes the CryptoHwAuditor to the correct leaf skill based on target
  (RNG entropy on a captured sample, firmware version regression, or a
  post-incident postmortem for a shipped hardware wallet / HSM / TPM).
allowed-tools: Bash Read Write
metadata:
  subdomain: crypto-hw
  when_to_use: "Coldcard, Trezor, Ledger, hardware wallet, HSM, TPM, YubiKey, TRNG, PRNG, entropy, seed phrase, BIP39, firmware regression, RNG bug, side-channel"
  tags:
    - hardware-wallet
    - trng
    - prng
    - entropy
    - firmware-regression
    - post-quantum
    - pqc
    - incident-response
  mitre_attack: T1552.004, T1622
---

# Cryptographic Hardware Audit Suite — Auditor Index

> Ethical / defensive framing. This suite audits shipped hardware for
> RNG weakness and firmware regressions so operators can rotate keys,
> add passphrase bits, or replace vulnerable devices **before**
> attackers reach them. It never attempts to extract keys from
> third-party wallets and never accepts a target without explicit
> owner consent recorded in `plan/roe.json:machine_enforcement.crypto_hw.authorized`.

## Playbook table

| Leaf skill | Trigger | Primary MITRE | Status |
|---|---|---|---|
| [rng-entropy-audit](rng-entropy-audit/SKILL.md) | RNG sample or seed spec in scope | T1552.004 | shipped v1.0.8 |

## Reference incident — Coldcard (2026)

The Coldcard Mk2 / Mk3 firmware regression (post-March 2021) silently
disabled the on-board TRNG and fell back to a deterministic software
PRNG seeded from stable device state. Reconstructing the seed became a
brute-force problem over a small key space rather than a cryptographic
one. The postmortem in `rng-entropy-audit/references/hardware-wallet-postmortem-template.md`
is structured after Block's public writeup so a fresh incident can be
walked through the same lanes without rediscovering the framework.

## Selection tree

```
if scope names an RNG sample (hex/base64) or a seed spec (word count + passphrase bits)
  → rng-entropy-audit

if scope names a firmware regression between two versions
  → rng-entropy-audit (compare-mode; take one sample per firmware)

if scope is a post-incident writeup for a shipped device
  → rng-entropy-audit + open the postmortem template in references/
```

## Authorization gate

```
authorized = plan/roe.json:machine_enforcement.crypto_hw.authorized
target_class = plan/roe.json:machine_enforcement.crypto_hw.target_class
              ("owned_device" | "vendor_sample" | "post_incident_reproduction")

  authorized == false OR missing   → refuse; produce no findings on that target
  authorized == true               → verify target_class matches the actual
                                     device provenance recorded in the RoE
```

## KG node contract

The auditor writes these node types when a KG middleware is enabled:

| Node kind | Typical props |
|---|---|
| `Device`  | vendor, model, firmware_version, chip |
| `RngSample` | source (trng \| prng \| unknown), bytes_captured, sha256 |
| `Finding` | title, severity, cvss, cwe, remediation |
| `Seed`   | word_count, checksum_scheme, passphrase_bits, effective_bits |
