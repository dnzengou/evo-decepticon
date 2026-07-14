# Decepticon — Blueprint

**Version:** 1.0.6
**Date:** 2026-07-14
**Live URL:** [decepticon.red](https://decepticon.red)
**Docs:** [docs.decepticon.red](https://docs.decepticon.red)
**Repo:** [PurpleAILAB/Decepticon](https://github.com/PurpleAILAB/Decepticon)

Single source of truth for what Decepticon is, what it ships, and what's next.
Source of truth for version = `pyproject.toml`. Bump here after bumping there.

---

## What Decepticon is

An **autonomous Red Team agent** that executes realistic attack chains (recon
→ exploit → post-exploit → C2) inside a hardened Kali sandbox on an isolated
Docker network. Every action operates under a written OPPLAN + RoE. The
**EvoMetaClaw** flywheel makes the skill library measurably better on each
engagement.

**Two moats:**
1. **Skills that get better on your data** — trajectory-data flywheel.
   Static registries can be copied in a week; a Q-gate trained on your
   engagement corpus cannot.
2. **Real interactive-shell orchestration** — persistent tmux sessions with
   automatic prompt detection, not one-shot bash fire-and-forget.

---

## Architecture snapshot

- **Python 3.13** package (`decepticon/`) — LangGraph agents + middleware
  + tools.
- **Next.js 16 web dashboard** (`clients/web/`) — Prisma + Postgres, Neo4j
  visualization, React Three Fiber 3D agent canvas.
- **LiteLLM proxy** for LLM routing with Anthropic/OpenAI/Gemini fallback.
- **Two isolated Docker networks** — management (`decepticon-net`) has zero
  reach into operations (`sandbox-net`).

---

## Agents (17 total)

| Phase | Agent | Skill sources |
|-------|-------|---------------|
| Orchestration | decepticon, soundwave | `skills/decepticon/`, `skills/soundwave/` |
| Reconnaissance | recon, scanner | `skills/recon/`, `skills/scanner/` |
| Exploitation | exploit, exploiter, detector, verifier, patcher | `skills/exploit/`, `skills/vulnresearch/` |
| Post-Exploitation | postexploit | `skills/post-exploit/` |
| Defense | defender | Offensive Vaccine loop |
| Domain Specialists | ad_operator, cloud_hunter, contract_auditor, reverser, analyst, **gnss_auditor** | see skill catalog |

`gnss_auditor` new in v1.0.6 — TESLA/OSNMA/HAS PQC audit + jamming/spoofing resilience.

---

## Distribution Channels

| Channel | Status | Ship path |
|---------|--------|-----------|
| PyPI wheel | ✅ v1.0.6 | `pip install decepticon` (once published) |
| Docker Compose stack | ✅ | `docker-compose.yml` + `docker-compose.watch.yml` |
| Install script | ✅ | `curl -fsSL https://decepticon.red/install \| bash` |
| Web dashboard | ✅ | `clients/web/` — Next.js 16, deploy anywhere |
| CLI launcher | ✅ | `clients/cli/`, `clients/launcher/` |
| LangGraph Platform | ✅ | `langgraph.json` — 18 assistants exposed |
| VS Code / Cursor MCP | 🔲 | planned — expose GNSS auditor + KG tools |
| Homebrew formula | 🔲 | planned |
| Community Skill Marketplace | 🔲 | planned — third-party skills via manifest |

---

## Roadmap

### v1.0.6 (2026-07-14) — GNSS + EvoMetaClaw cut

- ✅ EvoMetaClaw core (`decepticon/core/evo_metaclaw.py`) — Q-gate + GRPO buffer + circuit-breaker + file-backed genome store
- ✅ EvoMetaClaw signal capture wired into `engagement_loop.py` (exception-swallowed, never breaks the loop)
- ✅ GNSS Auditor agent (`decepticon/agents/gnss_auditor.py`) + prompt
- ✅ TESLA PQC audit skill (`skills/gnss/tesla-pqc-audit/`) with `mac_grover_score.py` + `root_sig_audit.py`
- ✅ Signal-disruption skill (`skills/gnss/signal-disruption/`) with authorization gate + `walk_spoof.py`
- ✅ 14 unit tests for evo_metaclaw core, all green (< 0.1s)
- ✅ 30-second commercial demo (`demo/gnss_pqc_demo.py`)
- ✅ Strategic moat doc (`docs/evo-metaclaw.md`)
- ✅ Web AGENTS registry gained `gnss_auditor` entry (satellite icon, Domain Specialist role)
- ✅ langgraph.json + docs/skills.md + docs/agents.md + README.md updated

### v1.1 (planned)

- 🔲 Live TESLA fixture parser (real DSM-PKR from Galileo test vectors)
- 🔲 TTLOF sweep automation script for `signal-disruption`
- 🔲 Neo4j edge type for `(genome)-[:EVOLVED_FROM]->(genome)` — makes lineage graph queryable
- 🔲 Nightly Q-gate offline retraining job over `signals.jsonl`
- 🔲 EvoMetaClaw `record_signal` populates `tokens_used` via observability middleware (currently 0)
- 🔲 HAS ICD Annex D parser reference in `skills/gnss/tesla-pqc-audit/references/`

---

## File Manifest — v1.0.6 additions

```
BLUEPRINT.md                                          NEW  this file
README.md                                             MOD  GNSS section, demo, moat, badges
.gitignore                                            MOD  evo-metaclaw-run/
langgraph.json                                        MOD  gnss_auditor graph
decepticon/core/evo_metaclaw.py                       NEW  moat core (~280 loc)
decepticon/core/engagement_loop.py                    MOD  Signal capture hook
decepticon/agents/gnss_auditor.py                     NEW  specialist agent
decepticon/agents/prompts/gnss_auditor.md             NEW  agent prompt
skills/gnss/tesla-pqc-audit/SKILL.md                  NEW  TESLA/PQC playbook
skills/gnss/tesla-pqc-audit/scripts/mac_grover_score.py   NEW
skills/gnss/tesla-pqc-audit/scripts/root_sig_audit.py     NEW
skills/gnss/tesla-pqc-audit/references/pqc-signature-comparison.md   NEW
skills/gnss/signal-disruption/SKILL.md                NEW  jamming/spoofing playbook
skills/gnss/signal-disruption/scripts/walk_spoof.py   NEW
skills/gnss/signal-disruption/references/rf-safety-checklist.md      NEW
demo/gnss_pqc_demo.py                                 NEW  30-second commercial demo
docs/evo-metaclaw.md                                  NEW  strategic moat writeup
docs/skills.md                                        MOD  gnss/ category + agent mapping
docs/agents.md                                        MOD  GNSS Auditor row
clients/web/src/lib/agents.ts                         MOD  gnss_auditor display entry
tests/unit/core/test_evo_metaclaw.py                  NEW  14 tests, all green
```

---

## Quality gates

| Gate | Status |
|------|--------|
| `pytest tests/unit/core/test_evo_metaclaw.py` | ✅ 14/14 in 0.08s |
| `python -c "import ast; ast.parse(open(f).read())"` on all new .py | ✅ |
| `json.load(open('langgraph.json'))` | ✅ |
| `python demo/gnss_pqc_demo.py` | ✅ 3 findings, 2 P0 |
| Skills conform to frontmatter spec (name, description) | ✅ |
| Authorization gate on physical-layer skills | ✅ enforced first |
| EvoMetaClaw signal capture is exception-swallowed | ✅ |
| No hardcoded secrets in new code | ✅ |

---

## Changelog

### v1.0.6 — 2026-07-14
- New: EvoMetaClaw core + engagement-loop signal capture (moat)
- New: GNSS Auditor agent + `skills/gnss/` category (TESLA/PQC + jam/spoof)
- New: 30-second commercial demo
- New: BLUEPRINT.md, docs/evo-metaclaw.md
- Test: 14 new unit tests for evo_metaclaw core
- UX: web dashboard shows GNSS Auditor tile

### v1.0.x — prior
Historical entries live in `pyproject.toml` version bumps and git tags.
This BLUEPRINT.md starts its versioned changelog at v1.0.6 — earlier
history is authoritatively captured by the git log.
