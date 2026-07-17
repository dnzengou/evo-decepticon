# Decepticon Demos

Two zero-setup demos that show what Decepticon's GNSS Auditor and
EvoMetaClaw flywheel do without installing the full stack.

## GNSS Auditor — interactive browser UI

**Live:** [https://dnzengou.github.io/evo-decepticon/](https://dnzengou.github.io/evo-decepticon/) (once GitHub Pages is enabled)

**Local:** open [`gnss_auditor_ui.html`](gnss_auditor_ui.html) directly in a browser.

Runs the three GNSS Auditor helpers client-side against synthetic inputs:

- **Grover-gap MAC verdict** — TESLA MAC + hash + tag-bits + rotation → post-Grover margin.
- **Root-signature verdict** — RSA / ECDSA / Dilithium / Falcon → PQC readiness score.
- **Receiver walk-off verdict** — jam/spoof walk-off vs receiver envelope with/without OSNMA.

Offline-safe: `default-src 'none'` CSP, no network, no eval, no data URIs.

## GNSS PQC — 30-second CLI demo

```bash
python demo/gnss_pqc_demo.py
```

Runs in < 1 s. Emits 3 findings (2 from `tesla-pqc-audit`, 1 from
`signal-disruption`), all HIGH severity. Zero external dependencies —
imports the three helpers directly from the skill folders.

Intended for sales-engineering laptop demos and CI smoke-testing that
the helpers still emit the expected finding shape.
