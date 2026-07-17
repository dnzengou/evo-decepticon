# Decepticon — Blueprint

**Version:** 1.0.7
**Date:** 2026-07-18
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
| GNSS Auditor demo (static) | ✅ v1.0.7 | GitHub Pages — [dnzengou.github.io/evo-decepticon](https://dnzengou.github.io/evo-decepticon/) |

---

## Roadmap

### v1.0.7 (2026-07-18) — Demo enablement (DEMO-BEFORE-INSTALL)

- ✅ `demo/gnss_auditor_ui.html` interactive UI landed on `main` (was stranded on feat branch)
- ✅ `demo/index.html` redirect landing (Pages root → GNSS Auditor demo)
- ✅ `demo/README.md` explains both demos (browser UI + CLI)
- ✅ README ▶ Try the GNSS Auditor demo CTA above Install (DEMO-BEFORE-INSTALL)
- ✅ `.github/workflows/demo-pages.yml` publishes `demo/` subtree to GitHub Pages on push
- ✅ `docs/user-manual.md` landed on `main` (was stranded on feat branch)

### v1.0.6 (2026-07-14) — GNSS + EvoMetaClaw cut

- ✅ EvoMetaClaw core (`packages/decepticon/decepticon/core/evo_metaclaw.py`) — Q-gate + GRPO buffer + circuit-breaker + file-backed genome store (~280 loc, stdlib-only)
- ✅ EvoMetaClaw signal capture in `packages/decepticon/decepticon/core/engagement_loop.py` (exception-swallowed at every call site, never breaks the loop)
- ✅ GNSS Auditor agent (`packages/decepticon/decepticon/agents/standard/gnss_auditor.py`) + prompt + role registered in `SLOTS_PER_ROLE`
- ✅ TESLA PQC audit skill (`packages/decepticon/decepticon/skills/standard/gnss/tesla-pqc-audit/`) with `mac_grover_score.py` + `root_sig_audit.py` + `references/pqc-signature-comparison.md`
- ✅ Signal-disruption skill (`packages/decepticon/decepticon/skills/standard/gnss/signal-disruption/`) with authorization gate + `walk_spoof.py` + `rf-safety-checklist.md`
- ✅ 16 unit tests for evo_metaclaw core, all green (0.08s)
- ✅ 30-second commercial demo (`demo/gnss_pqc_demo.py`) — 3/3 HIGH findings, < 1 s
- ✅ Strategic moat doc (`docs/evo-metaclaw.md`)
- ✅ Web AGENTS registry gained `gnss_auditor` entry (cyan, Domain Specialist role)
- ✅ `langgraph.json` now serves 20 graphs (added `gnss_auditor`); `.gitignore` covers `evo-metaclaw-run/`

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
BLUEPRINT.md                                                                   NEW  this file
.gitignore                                                                     MOD  evo-metaclaw-run/
langgraph.json                                                                 MOD  gnss_auditor graph (20 total)
packages/decepticon/decepticon/core/evo_metaclaw.py                            NEW  moat core (~280 loc, stdlib only)
packages/decepticon/decepticon/core/engagement_loop.py                         NEW  signal-capture driver (exception-swallowed)
packages/decepticon/decepticon/agents/standard/gnss_auditor.py                 NEW  specialist agent factory + SUBAGENT_SPEC
packages/decepticon/decepticon/agents/prompts/standard/gnss_auditor.md         NEW  agent system prompt
packages/decepticon-core/decepticon_core/contracts/slots.py                    MOD  gnss_auditor role added to SLOTS_PER_ROLE
packages/decepticon/decepticon/skills/standard/gnss/SKILL.md                   NEW  suite index
packages/decepticon/decepticon/skills/standard/gnss/tesla-pqc-audit/SKILL.md   NEW  TESLA/PQC playbook
packages/decepticon/decepticon/skills/standard/gnss/tesla-pqc-audit/mac_grover_score.py         NEW
packages/decepticon/decepticon/skills/standard/gnss/tesla-pqc-audit/root_sig_audit.py           NEW
packages/decepticon/decepticon/skills/standard/gnss/tesla-pqc-audit/references/pqc-signature-comparison.md   NEW
packages/decepticon/decepticon/skills/standard/gnss/signal-disruption/SKILL.md                  NEW  jam/spoof playbook
packages/decepticon/decepticon/skills/standard/gnss/signal-disruption/walk_spoof.py             NEW
packages/decepticon/decepticon/skills/standard/gnss/signal-disruption/rf-safety-checklist.md    NEW
demo/gnss_pqc_demo.py                                                          NEW  30-second commercial demo (< 1 s)
docs/evo-metaclaw.md                                                           NEW  strategic moat writeup
clients/web/src/lib/agents.ts                                                  MOD  gnss_auditor display entry (cyan tile)
tests/unit/core/test_evo_metaclaw.py                                           NEW  16 tests, all green
```

---

## Quality gates

| Gate | Status |
|------|--------|
| `DECEPTICON_SKIP_BOOT=1 pytest tests/unit/core/test_evo_metaclaw.py` | ✅ 16/16 in 0.08s |
| `python -c "import ast; ast.parse(open(f).read())"` on all 8 new .py | ✅ |
| `json.load(open('langgraph.json'))` — 20 graphs | ✅ |
| `python demo/gnss_pqc_demo.py` | ✅ 3 findings, 3 HIGH |
| Skills conform to frontmatter spec (name, description) | ✅ 3/3 |
| Authorization gate on physical-layer skills | ✅ `walk_spoof.py` refuses without RoE |
| EvoMetaClaw signal capture is exception-swallowed | ✅ `EngagementLoop.step` catches |
| No hardcoded secrets in new code | ✅ |

---

## Changelog

### v1.0.7 — 2026-07-18
- New: `demo/index.html` (redirect landing) + `demo/README.md`
- New: `.github/workflows/demo-pages.yml` — static Pages deploy of `demo/` subtree
- New: README CTA "▶ Try the GNSS Auditor demo" (above Install)
- Ship: `demo/gnss_auditor_ui.html` + `docs/user-manual.md` cherry-picked from `feat/gnss-auditor-evo-metaclaw` (previously merged PRs #1 and #2 dropped them)
- Distribution: new "GNSS Auditor demo (static)" channel via GitHub Pages

### v1.0.6 — 2026-07-14
- New: EvoMetaClaw core + engagement-loop signal capture (moat)
- New: GNSS Auditor agent + `skills/standard/gnss/` category (TESLA/PQC + jam/spoof)
- New: 30-second commercial demo (`demo/gnss_pqc_demo.py`)
- New: BLUEPRINT.md, docs/evo-metaclaw.md
- Test: 16 new unit tests for evo_metaclaw core (0.08 s)
- UX: web dashboard shows GNSS Auditor tile (cyan, Domain Specialist)

### v1.0.x — prior
Historical entries live in `pyproject.toml` version bumps and git tags.
This BLUEPRINT.md starts its versioned changelog at v1.0.6 — earlier
history is authoritatively captured by the git log.
