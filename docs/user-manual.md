# Decepticon — User Manual

End-to-end guide: install → configure → run your first engagement →
read findings. Covers both the Docker stack (recommended) and the
lightweight "just play with GNSS PQC" quickstart that needs **no
Docker, no Kali, no LLM key**.

**Version target:** v1.0.6 (EvoMetaClaw + GNSS Auditor cut).

---

## 0. Prerequisites

| Track | You need |
|---|---|
| **GNSS-only quickstart** (< 2 min) | Python 3.13 |
| **Full engagement stack** | Docker + Docker Compose v2, ≥ 8 GB RAM, an LLM provider key |
| **From source** (contributors) | + `uv` (or `pip`), Node 20+ for the web dashboard |

Supported OS: macOS (Apple Silicon + Intel), Linux (amd64 + arm64), Windows (native or WSL2 Ubuntu / Kali).

---

## 1. Install

### 1a. GNSS-only quickstart (no Docker, no LLM key)

For sales-eng laptop demos, CI smoke tests, or just kicking the tires
on the PQC audit helpers:

```bash
git clone https://github.com/PurpleAILAB/Decepticon.git
cd Decepticon
python demo/gnss_pqc_demo.py       # < 1 s; prints 3 HIGH findings
```

Or open the browser UI (no server needed — it's a single self-contained HTML file):

```bash
# macOS
open demo/gnss_auditor_ui.html
# Linux
xdg-open demo/gnss_auditor_ui.html
# Windows
start demo/gnss_auditor_ui.html
```

The UI runs the same three helpers client-side. No network calls, no
data leaves your machine, no `<script src="...">` fetches.

### 1b. Full stack — bash installer

```bash
curl -fsSL https://decepticon.red/install | bash
decepticon onboard                 # interactive wizard: pick provider, paste API key
decepticon                         # brings up the core stack + drops you in the CLI
```

### 1c. Full stack — Windows PowerShell

```powershell
irm https://decepticon.red/install.ps1 | iex
decepticon onboard
decepticon
```

### 1d. From source (contributors)

```bash
git clone https://github.com/PurpleAILAB/Decepticon.git
cd Decepticon
make dogfood                       # full OSS UX on local code
# or:
make dev                           # backend hot-reload (compose watch)
```

### 1e. As a library (pip)

Building on top of the agents:

```bash
pip install decepticon             # core SDK
pip install "decepticon[neo4j]"    # + the knowledge-graph tools
```

The SDK routes LLM calls to a LiteLLM proxy (`DECEPTICON_LLM__PROXY_URL`)
and sandbox execution to a runtime service (`SANDBOX_URL`). Run those
via the Docker stack above, or point at your own equivalents.

---

## 2. Configure

The `decepticon onboard` wizard writes `.env` from `.env.example`. If
you skip the wizard, at minimum set:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | Any one — the LiteLLM proxy fans out |
| `DECEPTICON_MODEL_PROFILE` | `eco` (default), `max`, or `test` |
| `DECEPTICON_PLUGINS` | Comma-separated bundle names — defaults to `standard,plugins` |
| `EVO_METACLAW_RUN_DIR` | Trajectory log dir — default `evo-metaclaw-run/` (gitignored) |

For the GNSS Auditor specifically, if you plan to run authorized TX:

```json
// plan/roe.json
{
  "machine_enforcement": {
    "rf": {
      "gnss": {
        "authorized": true,
        "fixture_id": "chamber-lab-A-2026Q3",
        "operator_licence_ref": "ITU-EX-12345"
      }
    }
  }
}
```

**Without this block, every physical-layer skill refuses `--tx` mode
and returns `outcome=blocked`.**

---

## 3. Run your first engagement

### 3a. Via the CLI

```bash
decepticon
> Set target to https://demo.testfire.net and start a recon-only engagement.
```

The orchestrator will:

1. Draft an OPPLAN + RoE, ask you to approve.
2. Spawn `recon` → gather.
3. Persist findings under `findings/<engagement-id>/FIND-NNN.md`.
4. Emit an EvoMetaClaw signal per subagent turn to
   `evo-metaclaw-run/signals.jsonl`.

### 3b. Via the LangGraph platform directly

```bash
langgraph dev                      # brings up the platform on :2024
```

Then hit any of the 20 graphs (open http://localhost:2024) — including
the new `gnss_auditor`.

### 3c. Just the GNSS Auditor (offline PQC audit)

The three GNSS helpers are stdlib-only Python scripts. Call them
directly on a captured slot spec:

```bash
cd packages/decepticon/decepticon/skills/standard/gnss/tesla-pqc-audit

# Score a MAC slot against Grover
python mac_grover_score.py --slot my_slot.json

# Audit a DSM-PKR root signature
python root_sig_audit.py --alg rsa --bits 2048 --key-id GAL-ROOT-2024

# Simulate a walk-off spoof scenario (safe by default)
cd ../signal-disruption
python walk_spoof.py --dry-run --receiver-model ublox-f9p \
    --walk-mps 1.0 --duration-s 120
```

---

## 4. Read findings

Findings live under `findings/<engagement-id>/`:

```
findings/2026-07-14T13-20Z/
├── FIND-001.md                    # each finding is a self-contained MD
├── FIND-002.md
└── gnss/
    ├── FIND-001.md                # GNSS-specific: PQC gap, TTLOF, etc.
    └── 2026-07-14T13-25Z-capture/ # I/Q + slot JSONs from the audit
```

Every finding follows the shared `finding-protocol` skill: severity
(CVSS v4.0), evidence chain, remediation. GNSS findings additionally
carry `spec_version` + a raw JSON `evidence:` block so downstream
receivers can be re-audited without re-running the capture.

---

## 5. Understand EvoMetaClaw

The trajectory-data flywheel writes two append-only files under
`evo-metaclaw-run/`:

- `genomes.jsonl` — each `(role, skill-set, prompt-hash)` proposed to the field.
- `signals.jsonl` — one row per subagent turn: `(genome_id, state_hash, action, reward, tokens_used, wallclock_s, success)`.

The Q-gate retrains offline over `signals.jsonl` and updates weights
that the next `EvoMetaClaw.propose()` call reads. In dev / OSS
distributions this is manual; in the commercial deploy it's a nightly
cron. Circuit breaker + genome cooldown are always on.

**Nothing leaves your tenancy.** No upload, no telemetry, no signal
export. The gitignore covers the run dir; the engagement loop has no
upload path. See **[EvoMetaClaw moat writeup](evo-metaclaw.md)** for
the "what competitors cannot copy" framing.

---

## 6. Safety model — what the tool refuses to do

| Refuse | Guard |
|---|---|
| Open-air GNSS TX | `plan/roe.json:machine_enforcement.rf.gnss.authorized` must be `true` AND fixture_id must match |
| Any action outside RoE scope | `RoEGuardrailMiddleware` blocks before the sandbox executes |
| Skills without the shared `finding-protocol` load | `SkillsMiddleware` verifies |
| Tools without RoE authorization | Per-tool `machine_enforcement` gates in the middleware stack |
| ICS / OT writes without lab annotation | `ics_operator` prompt mandates canary-only |
| Escaping the sandbox | Docker network isolation (`decepticon-net` ⇄ `sandbox-net` is one-way) |

The safety story is **layered**: prompt discipline + middleware
enforcement + Docker network isolation + `plan/roe.json` machine
enforcement. Any single layer failing does not open the whole surface.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: pydantic_settings` in tests | Missing dev dep | `pip install pydantic-settings` or `uv sync` |
| GNSS demo prints garbage instead of arrows/emoji | Windows CP-1252 shell | `chcp 65001` first, or use PowerShell 7 |
| `walk_spoof.py --tx` refuses | No RoE authorization block | Add the `machine_enforcement.rf.gnss` block above |
| `langgraph dev` says `gnss_auditor` graph missing | Old `langgraph.json` | `git pull`; check the file has 20 graphs |
| Engagement dies mid-turn | LLM key exhausted | The fallback chain should catch this; check `LLM_ROUTER_LOGS=1` |

---

## 8. Where to go next

- **[Engagement workflow deep dive](engagement-workflow.md)** — full RoE / ConOps / OPPLAN pipeline.
- **[Skills format spec](skills.md)** — write your own SKILL.md files.
- **[Web Dashboard](web-dashboard.md)** — Neo4j visualization + React Three Fiber canvas.
- **[Contributing](contributing.md)** — how to land a PR.
- **[Discord](https://discord.gg/TZUYsZgrRG)** — talk to the maintainers.

If you find a rough edge in this manual, open a PR against
`docs/user-manual.md`.
