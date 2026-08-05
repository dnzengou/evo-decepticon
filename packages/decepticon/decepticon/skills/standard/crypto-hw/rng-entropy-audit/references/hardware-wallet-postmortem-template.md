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
- **[../../firmware-guard-audit/SKILL.md](../../firmware-guard-audit/SKILL.md)** — root-cause pattern scanner (defined-but-zero guards, weak-symbol RNG)
- **[../../seed-recovery-poc/SKILL.md](../../seed-recovery-poc/SKILL.md)** — owner-consent PoC that proves enumerability
- **[../../SKILL.md](../../SKILL.md)** — suite index

---

## Reference incident — Coldcard, 2026-07-05

Concrete instance of everything above. Retain as the worked example the
template is calibrated against.

### Header (worked example)

- **Device**: Coinkite Coldcard — Mk2, Mk3, Mk4, Q, Edge
- **Firmware range affected**:
  - Mk2 / Mk3 firmware **4.1.9 through 4.4.1** (Coldcard's release numbering)
  - Mk4 / Q **pre-5.4.0**
  - Edge **pre-6.6.66**
- **Firmware fixed in**: Mk2/Mk3 4.4.2+; Mk4/Q 5.4.0; Edge 6.6.66
- **Regression landed**: March 2021 (bug shipped, sat for four-plus years)
- **First exploitation**: 2026-07-05, 01:19 UTC — 01:51 UTC (41 minutes)
- **Loss**: 1,082.85 BTC (~70 M USD) from 1,196 wallets across six blocks
- **Blast radius**: seeds generated on the affected firmware; safe if the
  seed pre-dates March 2021 OR the seed was mixed with owner dice rolls
  (≥ 50 bits) OR a strong unique passphrase was used
- **CVSS**: 9.1 (AV:P / AC:H / PR:N / UI:N / S:C / C:H / I:H / A:N) —
  physical attack complexity waived because the seed is enumerable
  off-device once known-state parameters are pinned

### Technical root cause (Coldcard)

Two-bug interaction, either alone would have been survivable.

**A. `#ifndef` guard where `#if` was required.**

```c
// board config file:
#define MICROPY_HW_ENABLE_RNG   (0)

// RNG driver file:
#ifndef MICROPY_HW_ENABLE_RNG
  hw_rng_init();      // <-- never runs. Guard sees the macro IS defined.
#endif
```

The guard checks *presence* of the macro, not *truth*. Config defined
`MICROPY_HW_ENABLE_RNG` to zero because Coldcard's product had two
separate hardware paths for RNG; the intent was to disable MicroPython's
built-in RNG and use Coldcard's own. The guard misread that intent.

**B. Same-name symbol resolution.**

The crypto library asked for a symbol named `rng_get()`. Coldcard's
hardware driver exported it as a differently-named function (e.g.
`ck_hw_rng_get()`). The MicroPython software fallback happened to
export `rng_get()`. The linker resolved the symbol to the fallback
silently — no warning, no error, both had the same signature.

**C. Fallback seeded from device-stable values.**

The MicroPython fallback (a small-state LCG-shaped PRNG) is seeded
once at first boot from:

- `OSC` — factory-set silicon serial (partly visible in USB serial number → attacker pins ~24 bits)
- `SYSTICK` — monotonic tick counter (~few bits from a boot-timing observation)
- `RTC` — real-time clock, second precision (~few bits from any first-use timestamp)

Coldcard's own postmortem number: ~40 effective bits against the
128-bit target. Enumerable on commodity hardware; near-instant with
domain knowledge.

### Attack model (Coldcard)

- Attacker generates candidate seeds on their own machine using the
  LCG state model above.
- Derives receive addresses under standard BIP32/BIP44 paths.
- Cross-references against the public blockchain — free lookup.
- A match means a funded wallet, and the standard sweep template
  drains it. The victim's device is never touched.
- **Fingerprint of a machine-driven sweep** (from the drained wallet
  distribution): three derivation paths tried at once — 1,163 modern
  format + seven older + six older-still. A human targeter would not
  touch three formats at a single victim. Scanner-shaped, not
  targeter-shaped.

### AI-model blind spot (added lesson from this incident)

Coldcard ran one of the strongest available LLMs across the codebase
weeks before the theft, looking specifically for RNG weaknesses. It
found nothing serious. The same class of AI cost an unrelated PQC
signature scheme half its strength in sixty hours (Anthropic research,
same week).

**Takeaway for the postmortem "Lessons" section**: attackers and
defenders now share the same class of tool. First-mover advantage is
narrow. This template's `firmware-guard-audit` and
`seed-recovery-poc` skills exist so the *pattern* — not just this
specific vulnerability — is checked automatically on every build
and on every published-disclosure trigger.

### Attribution note

The Coldcard attacker's opsec break: they used a paid account at a
mainstream blockchain-data provider to look up the source addresses
during the sweep. The provider's own logs matched the workflow with
unusual precision (order of requests, timing pattern) and were handed
to authorities. Attribution in security is one of the hardest
problems; a paid account with a matching log pattern is a lead, not
a name.
