---
name: rng-entropy-audit
description: Cryptographic-hardware RNG + seed entropy audit. Scores captured RNG output for bias / repetition / low min-entropy (TRNG→PRNG fallback like the Coldcard 2021 regression), and scores seed material (BIP39 word count + passphrase bits) for attack cost. Triggers on 'RNG bug', 'TRNG disabled', 'PRNG fallback', 'entropy audit', 'hardware wallet', 'seed strength', 'Coldcard', 'BIP39 passphrase'.
metadata:
  subdomain: crypto-hw
  when_to_use: "hardware wallet RNG audit, TRNG PRNG fallback detection, BIP39 seed entropy, seed passphrase strength, firmware regression against a prior sample, incident postmortem for a shipped device"
  upstream_ref: "NIST SP 800-90B (entropy sources), NIST SP 800-22 (statistical tests), BIP39"
---

# RNG + Seed Entropy Audit Skill

You are auditing whether a shipped cryptographic hardware device (wallet,
HSM, TPM, YubiKey-class token) actually delivers the entropy its data
sheet promises. The two inputs you accept are:

1. **A captured RNG sample** (hex or base64, at least 4 KiB recommended)
   pulled from the device's random-source endpoint under owner consent.
2. **A seed spec** (BIP39 word count + optional passphrase-bit estimate)
   the owner supplies for a strength check on their existing wallet.

The two outputs are structured findings that either (a) certify the
device / seed as meeting the 128-bit post-Grover strength target or
(b) name the concrete remediation (add passphrase bits, rotate to
audited firmware, migrate off the affected chip).

## Threat model (fixed — do not renegotiate)

- **TRNG-to-PRNG silent fallback** is the class of bug that took down
  Coldcard Mk2/Mk3 in the 2021 firmware regression. The RNG source
  quietly switches from a hardware entropy source to a deterministic
  software PRNG seeded from stable device state (serial, boot counter,
  flash pointer). Once one sample is available the state space shrinks
  to something a laptop can enumerate.
- **Under-truncated seeds**. BIP39 12-word seeds provide 128 bits of raw
  entropy (128-bit security). 24-word seeds provide 256 bits raw but
  are typically rated at 256 bits of *security* only because they
  reference a checksum that doesn't multiply security. Additional
  BIP39 passphrase bits are additive: each character of a
  cryptographically-random passphrase adds ~5.9 bits (95-char printable
  ASCII), each dictionary word from a 4096-word list adds 12 bits.
- **Grover halves the effective bits** of any brute-force target. A
  128-bit seed is 64 pq-bits under Grover; below the NIST Cat-1 128-bit
  target. The remediation (BEFORE Q-day) is to add passphrase bits —
  every doubling of effective bits doubles the Grover search cost.

## Operating principles

1. **Score before you speculate.** Run `rng_entropy_score.py --sample`
   on any captured RNG output and `rng_entropy_score.py --seed` on any
   seed spec. Findings ship with structured severity — no free-form
   speculation.
2. **Never accept a sample from an unverified source.** The value of the
   audit depends on the sample coming from the actual device under
   owner control. Ingest only samples with a chain-of-custody record
   in `findings/crypto-hw/<utc>-capture/provenance.json`.
3. **Never persist the sample or seed itself.** Only the SHA-256 of the
   sample and the *shape* of the seed (word count, passphrase bits)
   reach the KG. The raw bytes and any recovered mnemonic stay in
   the operator's ephemeral workspace and are shredded post-audit.
4. **Recommend passphrase bits BEFORE recommending device replacement.**
   Adding 8 – 16 bits of high-entropy passphrase is cheaper for the
   owner than migrating funds off a hardware wallet and buys time
   until a firmware fix or a PQC-native successor ships.

## Severity rubric — RNG sample

| Signal | Severity | Finding |
|---|---|---|
| min-entropy H_min ≥ 7.0 bits/byte AND monobit \|dev\| ≤ 4σ AND repeated-block ratio ≤ 0.01 | INFO | RNG output indistinguishable from ideal at this sample size. |
| H_min between 6.0 and 7.0 bits/byte OR monobit 4σ – 6σ | LOW | Detectable bias; increase sample size and re-audit. |
| H_min between 4.0 and 6.0 OR repeated 4-byte block ratio between 0.01 and 0.25 | MEDIUM | Structural bias present; likely non-cryptographic RNG. Do not use for key derivation. |
| H_min < 4.0 OR any all-zero / all-ones block ≥ 16 bytes OR repeated 4-byte block ratio > 0.25 | HIGH | RNG output is deterministic or has failed catastrophically. Do not use. Assume any key derived from this source is recoverable. |

> The repeated-4byte-block ratio is the primary Coldcard-class signal.
> A truly random sample of ~1024 four-byte blocks has essentially zero
> collisions in a 2^32 space (birthday bound), so any measurable
> collision rate signals a small effective state — the fingerprint of
> a software PRNG cycling in a tight loop.

## Severity rubric — seed spec

| Effective bits (post-passphrase) | Severity | Finding |
|---|---|---|
| ≥ 128 pq-bits (≥ 256 classical) | INFO | Meets NIST Cat-1 post-Grover target. |
| 96 – 127 pq-bits | LOW | Below Cat-1. Add 8–16 passphrase bits on next rotation. |
| 64 – 95 pq-bits | MEDIUM | Vulnerable to a CRQC by 2035. Rotate within 2 years OR add passphrase bits now. |
| < 64 pq-bits | HIGH | Recoverable pre-CRQC by nation-state targeting. Rotate immediately AND add ≥ 32 passphrase bits. |

## Workflow

```
1. Load spec + sample from findings/crypto-hw/<utc>-capture/.
   Require provenance.json (device serial, firmware version, capture
   method, operator identity).
2. If a sample is present:
     python rng_entropy_score.py --sample <sample.hex> [--label <name>]
   Emit finding = { source, bytes, min_entropy, monobit_dev, longest_run,
                    repeated_block_ratio, severity, rec }.
3. If a seed spec is present:
     python rng_entropy_score.py --seed --words <n> --passphrase-bits <b>
   Emit finding = { words, raw_bits, passphrase_bits, effective_bits,
                    pq_bits, severity, migration }.
4. If both firmware versions are supplied (regression mode):
     python rng_entropy_score.py --sample <before.hex> --label before
     python rng_entropy_score.py --sample <after.hex>  --label after
   Compare the two verdicts; emit an additional finding of severity
   HIGH if the "after" verdict is worse than "before" (regression).
5. Persist findings to findings/crypto-hw/FIND-NNN.md via the shared
   finding-protocol skill; each MUST cite provenance.json + the exact
   firmware version.
```

## Inputs

**RNG sample** (hex-encoded bytes):

```
$ python rng_entropy_score.py --sample sample.hex --label "coldcard-mk3-5.0.6"
```

**Seed spec**:

```
$ python rng_entropy_score.py --seed --words 24 --passphrase-bits 0
```

**Regression pair** (before / after firmware upgrade):

```
$ python rng_entropy_score.py --sample before.hex --label before
$ python rng_entropy_score.py --sample after.hex  --label after
```

## What NOT to do

- Do NOT run this skill on a captured sample without provenance.json.
  A sample from an unknown source proves nothing about the device;
  the audit collapses to "here is a byte string".
- Do NOT publish or transmit the sample bytes. Only the SHA-256 hash,
  size, and derived metrics are safe to persist. The sample itself
  can be device-identifying via subtle biases.
- Do NOT recommend a device replacement on a MEDIUM finding alone.
  Adding passphrase bits is faster, cheaper, and reversible.
- Do NOT run this skill on a target the operator does not own or does
  not have written consent to audit. Cryptographic hardware audits on
  third-party devices are out of scope regardless of RoE authorization.
