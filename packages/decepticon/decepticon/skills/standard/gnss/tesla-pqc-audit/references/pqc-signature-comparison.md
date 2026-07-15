# PQC Signature Comparison — GNSS Fit Reference

Reference table for choosing a post-quantum signature algorithm for
GNSS space-segment authentication (TESLA root, OSNMA DSM-PKR,
Galileo HAS, GPS Chimera, BeiDou B1C).

Numbers below are from **NIST FIPS 204** (ML-DSA, August 2024),
**FIPS 205** (SLH-DSA, August 2024), and the **Falcon Round 3 spec**
(draft FIPS 206). Sizes are exact bytes; classical bits use best
known cryptanalysis; pq bits assume Shor for RSA/ECDSA and NIST
category mapping for PQC families.

## Classical (Shor-vulnerable)

| Algorithm   | Pub-key (B) | Sig (B) | Classical bits | PQ bits | Verify (rel.) |
|-------------|-------------|---------|----------------|---------|---------------|
| RSA-2048    | 256         | 256     | 112            | **0**   | 1.0×          |
| RSA-3072    | 384         | 384     | 128            | **0**   | 2.3×          |
| RSA-4096    | 512         | 512     | 152            | **0**   | 4.1×          |
| ECDSA-P256  | 64          | 64      | 128            | **0**   | 0.4×          |
| ECDSA-P384  | 96          | 96      | 192            | **0**   | 1.1×          |
| ECDSA-P521  | 132         | 132     | 256            | **0**   | 3.0×          |

**All rows above are 0 pq-bits under a CRQC.** Any GNSS root signature
still on this list has an unbounded post-2035 forgery risk.

## Post-quantum — ML-DSA (FIPS 204, module-lattice)

| Parameter set | Pub-key (B) | Sig (B) | PQ bits | NIST Cat |
|---------------|-------------|---------|---------|----------|
| ML-DSA-44     | 1312        | 2420    | 128     | 2        |
| ML-DSA-65     | 1952        | 3293    | 192     | 3        |
| ML-DSA-87     | 2592        | 4595    | 256     | 5        |

ML-DSA is the default NIST recommendation. Sigs are ~3 KB — needs
multi-page SIS aggregation on Galileo I/NAV (128-bit pages) and fits
in one HAS subframe on HAS Issue 1.0.

## Post-quantum — SLH-DSA (FIPS 205, hash-based)

| Parameter set  | Pub-key (B) | Sig (B) | PQ bits | NIST Cat |
|----------------|-------------|---------|---------|----------|
| SLH-DSA-128s   | 32          | 7856    | 128     | 1        |
| SLH-DSA-128f   | 32          | 17088   | 128     | 1        |
| SLH-DSA-192s   | 48          | 16224   | 192     | 3        |
| SLH-DSA-192f   | 48          | 35664   | 192     | 3        |
| SLH-DSA-256s   | 64          | 29792   | 256     | 5        |
| SLH-DSA-256f   | 64          | 49856   | 256     | 5        |

SLH-DSA is the conservative choice — security reduces to the underlying
hash. Sig sizes are 8–50 KB, prohibitive for narrow-band SIS unless
transmitted out-of-band (e.g., ground-augmented). Use for TESLA root
where the root is fetched once from a signed almanac.

## Post-quantum — Falcon (draft FIPS 206, NTRU lattice)

| Parameter set | Pub-key (B) | Sig (B) | PQ bits | NIST Cat |
|---------------|-------------|---------|---------|----------|
| Falcon-512    | 897         | 666     | 128     | 1        |
| Falcon-1024   | 1793        | 1280    | 256     | 5        |

Smallest PQC signatures. Verification is fast (comparable to ECDSA-P256).
The tradeoff: signing requires floating-point arithmetic — a concern
only for the space segment, not the ground receiver.

## GNSS SIS-ICD payload fit

| Constellation      | Signed-page payload | Fit verdict                                                                 |
|--------------------|---------------------|------------------------------------------------------------------------------|
| Galileo E1B I/NAV  | 128 b × ~24 pages   | ML-DSA-65 needs ~11 pages aggregated. Falcon-512 fits in 5 pages.           |
| Galileo E6B HAS    | 448 b × subframe    | Falcon-512 fits one subframe. ML-DSA-44 needs 5 subframes.                  |
| GPS L1C / L2C      | 300 b subframe      | ML-DSA-65 needs ~11 subframes. Falcon-512 in 4.                             |
| GPS Chimera (LNAV) | 24 b × page (300 b) | Same as L1C; Chimera transmits sig over ~1 min interval.                    |
| BeiDou B1C         | 288 b subframe      | Similar to GPS L1C.                                                          |

**Practical migration recipe:**
- Space-segment root signatures where the payload budget is ≥ 4 KB:
  **ML-DSA-65** (Cat 3, 192 pq-bits).
- Narrow-band SIS with sub-1 KB per signed page: **Falcon-512** (Cat 1,
  128 pq-bits) with the caveat that the ground-station signer needs a
  side-channel-hardened float unit.
- TESLA root fetched offline from a signed almanac:
  **SLH-DSA-192s** (hash-based, conservative long-term). Root fetch
  isn't bandwidth-critical; the disclosed-key chain that follows is HMAC.

## Severity guide for `mac_grover_score.py` / `root_sig_audit.py`

- `INFO` — meets 128-bit pq target. No action.
- `LOW`  — below Cat-1 but above 96 pq-bits. Migrate at next spec rev.
- `MEDIUM` — 64–95 pq-bits. Rotate root within 2 y or migrate.
- `HIGH` — < 64 pq-bits or RSA / ECDSA root under Shor. Immediate.

## Change history

- 2026-07-14: Initial reference, aligned with FIPS 204/205 finals.
