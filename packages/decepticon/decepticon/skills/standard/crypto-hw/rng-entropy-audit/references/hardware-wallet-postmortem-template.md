# Hardware wallet incident postmortem — template

> Structured after Block's 2026 Coldcard writeup. Fill in each section
> with what is knowable from the RoE-authorized capture; leave sections
> marked `[unknown]` explicitly rather than guessing. A postmortem with
> `[unknown]` markers is more actionable than one with plausible-sounding
> filler.

## Header

- **Device**: vendor, product, model revision (e.g. Coldcard Mk3)
- **Firmware range affected**: from-version — to-version (e.g. 3.1.0 — 5.0.5)
- **Firmware fixed in**: version + release date
- **Reported by**: independent researcher / vendor / third-party auditor
- **Reported on**: YYYY-MM-DD
- **Public disclosure on**: YYYY-MM-DD
- **CVSS**: base score + vector
- **CWE**: e.g. CWE-338 (Use of cryptographically weak PRNG),
  CWE-330 (Use of insufficiently random values)

## Executive summary

One paragraph in operator language. What the bug is, who is affected,
what to do today, what to do this quarter.

## Technical root cause

- What component was miswired (chip register, firmware branch, driver
  path, boot sequence)?
- What was the SAFE prior state and what changed?
- Was the change intentional (feature) or accidental (regression)?
- Why did existing tests not catch it? (Missing sample-mode entropy
  test, missing regression test against prior firmware, missing
  hardware-in-loop CI, …)

## Attack model

- What does the attacker need to know? (device serial, boot counter,
  firmware version, RF proximity, physical possession, …)
- What is the state space the attacker enumerates? (bits of effective
  entropy)
- What is the wall-clock cost on commodity hardware today?
- What is the wall-clock cost under a plausible 2035 CRQC?

## Detection

Run `rng_entropy_score.py --sample <hex>` on a fresh capture from an
affected firmware:

```bash
python rng_entropy_score.py --sample <path> --label "<vendor>-<model>-<fw>" --pretty
```

Expected verdict for an actual-instance-of-this-bug capture:

- `severity: HIGH`
- Non-null `catastrophic_block` OR `min_entropy_bits_per_byte < 4.0`
  OR `repeated_4byte_block_ratio > 0.01`

If any capture from the affected firmware range does NOT produce a
HIGH verdict, either the capture provenance is wrong or the bug does
not manifest under the tested code path — record both cases explicitly.

## Remediation

Per-user (in priority order):

1. **Add passphrase bits** — score with `rng_entropy_score.py --seed
   --words <n> --passphrase-bits <b>` until the verdict is INFO.
2. **Rotate to a wallet generated on a device from the fixed firmware
   range**. Do this before adding significant new funds.
3. **Migrate off the affected device** if the vendor's fix window is
   longer than the user's threat model.

Per-vendor:

1. Ship the firmware fix and back-port to every product line that
   shares the miswired component.
2. Add a hardware-in-loop CI job that runs `rng_entropy_score.py` on
   the RNG endpoint of every release build, gated at severity <= LOW.
3. Publish a signed advisory with the CVSS + CWE header and the
   remediation ladder in operator language.

## Lessons

Freeform. What would have made this bug visible three months earlier?
Missing test class? Missing telemetry? Missing external audit? Missing
disclosure channel? Answer specifically; the point of this section is
to make the same class of bug uneconomic to ship again.

## Cross-references

- **NIST SP 800-90B** — entropy source assessment
- **NIST SP 800-22** — statistical randomness test suite (subset
  implemented in `rng_entropy_score.py`)
- **BIP39** — mnemonic seed word list + checksum scheme
- **[rng-entropy-audit/SKILL.md](../SKILL.md)** — this suite's rubric
- **[crypto-hw/SKILL.md](../../SKILL.md)** — suite index
