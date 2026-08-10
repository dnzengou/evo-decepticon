---
name: seed-recovery-poc
description: >
  Measured offensive capability — proves whether a hardware wallet the
  operator OWNS is genuinely enumerable given the Coldcard-class small
  entropy state (~2^32). Never returns a recovered seed, and never
  derives real bitcoin addresses; it uses a simulated derivation
  (HMAC-SHA256 over the candidate seed) to prove the state space is
  small enough to enumerate. Comparison happens against owner-supplied
  address hashes only. Gated on
  `plan/roe.json:machine_enforcement.crypto_hw.owner_consent==true`
  AND `target_class=="owned_device"`.
metadata:
  subdomain: crypto-hw
  when_to_use: "prove Coldcard-class enumerability on an owned device, verify a rotation was necessary before advising a user to migrate, run a consent-gated red-team exercise on your own hardware"
  ethical_class: "owner-consent-only PoC; never runs on third-party targets; never publishes recovered material"
---

# Seed Recovery PoC — Owner-Consent, Ethical, Measured

You are proving whether a Coldcard-class small-state seed weakness is
genuinely enumerable on a wallet the operator has explicit consent to
audit. The value of this PoC is that it converts "your wallet is
theoretically vulnerable" into "your wallet is provably enumerable at
this specific state" — which is what an owner needs before spending
time and money on migration.

## Non-negotiable gates (the skill refuses on any violation)

1. `plan/roe.json:machine_enforcement.crypto_hw.owner_consent == true`
   AND `target_class == "owned_device"`. Any other target class
   (`vendor_sample`, `post_incident_reproduction`, `third_party`) is
   REFUSED — the ethical framing of the PoC depends on the owner
   being the one running it.
2. Owner supplies address material as **SHA-256 hashes**, not as raw
   addresses or public keys. The comparison is `sha256(candidate_derivation)
   == owner_hash`. This means the skill never sees the wallet's real
   addresses, only their fingerprints.
3. The derivation function is `HMAC-SHA256(seed, "poc-derivation-v1")`.
   This is NOT bitcoin BIP32/BIP44 derivation. A match proves the
   enumeration is feasible; it does not produce bitcoin private keys.
4. Search-space cap defaults to 2^24 (16 M candidates). Raising the cap
   above 2^28 requires explicit `--acknowledge-cost` — enumerating
   2^32 candidates takes hours on commodity hardware and can be
   misused as a DoS.
5. The output is a boolean verdict (`{"vulnerable": true/false, ...}`).
   The recovered candidate seed value, if any, is **hashed and zeroed
   in place before the process returns**. The verdict includes the
   sha256 of the state parameters used, so the audit is reproducible
   without exposing the state itself.

## Threat model

Coldcard's post-2021 firmware seeded a MicroPython PRNG once at boot
from device-stable values:

- `OSC` — a factory-set serial value baked into the chip. Partly
  visible in the wallet's USB serial number, so an attacker sniffing
  a device event can pin ~24 bits.
- `SYSTICK` — a monotonic tick counter. Narrow-able to a small window
  by knowing when the device booted (charge cable insertion, LED
  flash, screen-on event visible in a store demo).
- `RTC` — real-time clock, wall-clock second precision. Narrow-able
  to a few thousand seconds by knowing the day of first use.

Combined bit-budget for a targeted attacker: ~40 bits (Coldcard's own
number). This PoC models the LCG the fallback used and enumerates
candidates in a specified range for `OSC × SYSTICK_range × RTC_range`.

## Workflow

```
1. Verify plan/roe.json gates before doing anything else.
   Refuse and return outcome=blocked if any gate fails.
2. Owner supplies (via findings/crypto-hw/<utc>-poc/):
     - hashes.txt : sha256 fingerprints of expected receive-address bytes,
                    one per line.
     - state.json : {"osc": <int>, "systick_range": [lo, hi],
                     "rtc_range": [lo, hi]}
     - Optional: --seed-length 32 (default) if the model deviates.
3. Run the PoC:
     python seed_recovery_poc.py \
       --hashes hashes.txt --state state.json --pretty
   Optionally --acknowledge-cost --search-cap 268435456 for a
   ~2^28 sweep. The default cap of 2^24 completes in seconds on a
   laptop and is enough to prove enumerability on a fully-known state.
4. Emit finding = { vulnerable: bool, candidates_tried: int,
                    search_cap: int, first_match_hash: sha256|null,
                    state_fingerprint: sha256, elapsed_seconds: float }.
5. If vulnerable == true: escalate to the owner immediately with the
   remediation ladder from rng-entropy-audit/SKILL.md (add passphrase
   bits, rotate seed on fixed firmware, migrate off if fix window is
   too long).
6. Wipe the recovered candidate from memory before returning. The
   finding never contains the seed value.
```

## What NOT to do

- Do NOT run this skill on a wallet you do not own. The RoE gate
  refuses, but the ethical framing depends on you, the operator,
  never asking. Consent is inherent to the audit's value.
- Do NOT connect this skill to any bitcoin library, BIP32/BIP44
  derivation, or blockchain data source. The simulated derivation
  is deliberate — it makes the PoC useful for proving enumerability
  without being usable for theft.
- Do NOT publish the state.json externally. Even without the seed,
  the state values are personal to the device and can help a real
  attacker (with a real derivation function) narrow their search.
- Do NOT report the recovered candidate in any log, telemetry, or
  finding. Even hashed. The skill only reports `vulnerable: true`
  and the boot-state fingerprint, never the seed itself.

## Interpreting the verdict

- `vulnerable == true, candidates_tried << search_cap`:
  The state space is tiny at the specified parameters. A real
  attacker with real BIP32 derivation would find your funds in
  the same wall-clock window this PoC found the match. Rotate
  immediately.
- `vulnerable == true, candidates_tried ~= search_cap`:
  Match found but only after a near-full sweep. Attackers with
  domain knowledge of your device's serial + boot window can still
  do this cheaply. Rotate.
- `vulnerable == false, candidates_tried == search_cap`:
  Either your device's state is genuinely larger than the sweep
  cap, OR your firmware's fallback is not the specific LCG this
  PoC models. Re-run the paired `rng-entropy-audit` skill on a
  captured RNG sample from the device to break the tie.

## Cross-references

- **[rng-entropy-audit/SKILL.md](../rng-entropy-audit/SKILL.md)** — companion detection skill (no exploit surface).
- **[firmware-guard-audit/SKILL.md](../firmware-guard-audit/SKILL.md)** — root-cause pattern detector.
- **[crypto-hw/SKILL.md](../SKILL.md)** — suite index.
