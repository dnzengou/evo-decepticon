---
name: firmware-guard-audit
description: Static-code audit for the Coldcard-class "defined-but-zero guard macro" firmware bug. Scans C/C++ firmware source for `#ifndef X_ENABLE` guards protecting hardware-RNG init when the same X_ENABLE is defined elsewhere as `0` (guard is silent while the feature is disabled). Also flags symbol-name aliasing hints where a hardware and a software function share a name and the linker silently picks the fallback. Triggers on 'firmware audit', 'MicroPython RNG', 'MICROPY_HW_ENABLE_RNG', 'ifndef guard', 'linker fallback', 'Coldcard firmware'.
metadata:
  subdomain: crypto-hw
  when_to_use: "firmware source audit for hardware-RNG init, MicroPython port review, defined-but-zero macro guard, weak-symbol linker fallback for RNG, Coldcard-shaped bugs"
  upstream_ref: "Coldcard incident 2026-07-05 (Mk2/Mk3 firmware 4.4.1 through 4.1.9, Mk4/Q pre-5.4.0 or Edge pre-6.6.66)"
---

# Firmware Guard-Macro Audit Skill

You are looking for the Coldcard-class firmware bug: a preprocessor guard
protecting hardware-RNG initialization fires "the setting isn't set" when
in fact the setting IS set — to zero. The guard reads `#ifndef X_ENABLE`,
sees `X_ENABLE` is defined, stays quiet, and the firmware builds without
the hardware RNG. Meanwhile a same-named software-fallback function
happens to satisfy the linker for the symbol the crypto library asks
for, so the build succeeds and the wallet ships. Nothing crashes.
Nothing warns. Every seed the wallet generates for the next four years
is enumerable from device-serial + timer state.

This skill runs OFFLINE against source files the owner or vendor
supplies. It never patches source; it emits findings for a human to
review + patch. No sandbox, no bash, no network egress required.

## Threat model (Coldcard 2026-07-05 postmortem)

- **Root cause A** — `#ifndef` where `#if` is required. A guard that
  fires on presence-of-macro instead of truth-of-macro silently
  disables the feature whenever the config file says `#define X 0`.
- **Root cause B** — same-symbol-name aliasing. The crypto library
  asks for `rng_get()` (or similar). The hardware driver exports
  `hw_rng_get()`. The MicroPython software fallback exports
  `rng_get()`. Linker picks the fallback; no warning, no error.
- **Root cause C** — the fallback is seeded ONCE per boot from
  device-stable values (OSC serial, SYSTICK ticks, RTC) instead of
  from a hardware entropy source. Once one seed is known the entire
  wallet's history is derivable.

## Signals this skill matches

**HIGH — defined-but-zero guard around hardware RNG init:**

```c
// somewhere in board config:
#define MICROPY_HW_ENABLE_RNG (0)

// somewhere in RNG driver:
#ifndef MICROPY_HW_ENABLE_RNG
  hw_rng_init();      // <-- never runs, because the macro IS defined
#endif
```

**HIGH — same-name RNG symbol exported by both hw and fallback:**

```
hw/rng.c:      uint8_t rng_get(void) { return read_trng_register(); }
soft/rng.c:    uint8_t rng_get(void) { return lcg_next(); }
```

(Weak-symbol variant, `__attribute__((weak))`, is even worse — no error
at link time even without `-Wl,--allow-multiple-definition`.)

**MEDIUM — build system quietly links a `soft/`/`fallback/` path when
`hw/` build is disabled** (Makefile / CMake OBJECTS conditional lists,
Kconfig `depends on` chains, MicroPython port `SRC_QSTR` selection).

## Workflow

```
1. Load the firmware source tree from findings/crypto-hw/<utc>-audit/src/.
   Require a provenance.json naming the vendor, product line, firmware
   version, and source commit hash.
2. Run the audit:
     python firmware_guard_audit.py --src <path> --pretty
   Emits one JSON finding with severity, matches (file:line), and
   the specific rule that fired.
3. For any HIGH finding, cite the exact snippet in the postmortem
   under the "Technical root cause" section (see
   ../rng-entropy-audit/references/hardware-wallet-postmortem-template.md).
4. Cross-check against a known-vulnerable-firmware version range table
   in the postmortem template. If the firmware being audited falls in
   the Coldcard-published range (Mk2/Mk3: 4.4.1 through 4.1.9 [note
   the descending numbering is intentional per Coldcard's release
   scheme]; Mk4/Q: pre-5.4.0; Edge: pre-6.6.66), auto-escalate to
   HIGH regardless of code match.
```

## What NOT to do

- Do NOT patch the source. Findings are advisory; the fix belongs
  to the vendor or the owner's firmware pinning workflow.
- Do NOT publish the source tree or any diff externally. The skill
  runs offline against source you have permission to read.
- Do NOT confuse this with a runtime audit. This skill sees the
  STATIC pattern; whether the pattern actually causes the hardware
  RNG to be skipped at runtime requires the `rng-entropy-audit`
  skill on a captured RNG sample from the built firmware. Both
  together give a HIGH-confidence verdict.
- Do NOT run this skill on binary firmware — you need source. For
  binary-only cases, escalate to the `reverser` specialist agent.
