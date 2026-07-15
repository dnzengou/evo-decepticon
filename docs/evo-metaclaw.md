# EvoMetaClaw — The Skill-Library Flywheel

**What it is.** A four-part evolutionary loop that turns every
Decepticon engagement into training data for the skill library the
next engagement will use. Trajectory data is the moat; the code is
the price of entry.

**Where it lives.**
[`packages/decepticon/decepticon/core/evo_metaclaw.py`](../packages/decepticon/decepticon/core/evo_metaclaw.py)
— stdlib-only, ~350 LoC. State persists under `evo-metaclaw-run/`
(gitignored — customer data never leaves the operator's tenancy).

---

## The four pieces

| Piece            | What it does                                                                 |
|------------------|------------------------------------------------------------------------------|
| `Genome`         | A candidate (role, skill-set, prompt-hash) config to try in the field.       |
| `QGate`          | Linear scorer over (reward, tokens_used, wallclock_s). Retrained offline.    |
| `GRPOBuffer`     | Rolling (state, action, reward, advantage) buffer with group-normalised advantages — the shape a downstream GRPO trainer consumes. |
| `CircuitBreaker` | Per-genome kill-switch on rolling failure rate. Cooldown before retry.       |

Composed by `EvoMetaClaw` façade with two hot-path calls:

- `propose(role, context) → Genome` — return a candidate to try, gated
  by the breaker and Q-gate.
- `record_signal(genome, outcome)` — persist to `signals.jsonl` and
  update buffer + breaker. Exception-swallowed in the engagement loop
  so the flywheel never breaks an active op.

---

## What competitors CAN copy in a week

Everything static:

- The **loader** (`load_skill()` middleware + progressive-disclosure YAML frontmatter).
- The **skill file shape** — `SKILL.md` + `references/*.md` + helper scripts.
- The **agent boilerplate** — LangGraph state, tool binding, prompt template.
- Even the **Q-gate math** — it's a linear scorer, and the seed weights
  are in `evo_metaclaw.py` in plaintext.
- The **circuit-breaker parameters** — window, threshold, cooldown are
  all constants.

**None of that is the moat.** OpenClaw can fork this repo and ship a
functional Decepticon in a sprint.

## What competitors CANNOT copy

**The Q-gate weights trained on your engagement corpus.** Retraining
runs nightly over `signals.jsonl` — the append-only log of every
(state, action, outcome) tuple your operators generate.

Why this is not copyable:

1. **You don't ship the trajectory data.** Signals live under
   `evo-metaclaw-run/` on the customer's tenancy. Never in the git
   repo, never in a build artifact, never in a customer-support
   attachment. `.gitignore` enforces this; the engagement loop has
   no upload path for signals.
2. **The signal distribution is a function of the customer's
   engagement mix.** A defence prime running GNSS pentests generates
   different signals than a fintech running LLM red-team ops. Each
   customer's Q-gate becomes progressively more useful for that
   customer's threat model — and less transferable to any other.
3. **The trajectory data compounds.** After 100 engagements the Q-gate
   has learned things like "for this customer's Kali fleet, skill X
   costs 2× the token budget of skill Y for equivalent reward on
   subdomain enumeration" — an insight that took real engagement time
   to produce. A fresh fork starts at generation 0.
4. **The genomes evolve on top of the Q-gate.** `Genome.parent_id` +
   `generation` track lineage. High-fitness genomes spawn variants;
   the population selects for what actually worked on real targets.
   The population state is co-owned by the operator, not the vendor.

---

## Failure modes of the moat

The moat is real but not indestructible. Defend it against:

- **Trajectory-data ex-fil.** A malicious operator with read access to
  `evo-metaclaw-run/` could sell the data to a competitor. Mitigations:
  encrypt-at-rest, per-role IAM on the run dir, audit-log every read.
- **Weight ex-fil via signed retrain artefacts.** If Q-gate weights are
  shipped between customer tenancies (e.g., for a "shared learning"
  offering), the moat leaks. Never ship trained weights across tenants
  without differential privacy on the signals.
- **Prompt-only clones.** A competitor could reproduce the *observable*
  behavior of a mature Q-gate by carefully authoring prompts based on
  public examples. Mitigation: keep the interesting genomes off any
  screencast, blog post, or public benchmark. The public artefact is
  always a stripped-down demo genome.
- **Static-registry regression.** If the retrain job fails silently
  and the Q-gate freezes on generation N for months, the moat stops
  compounding. Mitigations: alarm on retrain freshness, expose
  `generation` in the dashboard.

---

## What to tell buyers

Not: "we have skills."

Instead: "our skills get measurably better on **your** engagement
data — and yours only. We ship the loader, the Q-gate math, and the
initial skill catalog. What you keep is the Q-gate that has seen your
targets. A fork of our repo starts at generation 0; a fork of your
tenancy starts at your current generation and inherits every lesson
your operators have taught it."

## What the flywheel means for pricing

The trajectory-data moat is the argument for engagement-count-based
pricing rather than seat-based:

- Seat-based pricing lets a mature customer generate infinite
  trajectory data at fixed cost — value delivered scales but revenue
  doesn't.
- Engagement-count pricing (or "signals-per-month") ties revenue to
  the rate at which the customer's Q-gate is compounding. That's the
  correct value axis.

Buyer objection to watch for: "we don't want to send you trajectory
data." **You don't need to.** The Q-gate retrains inside the customer
tenancy. The vendor gets `generation` + `signal_count` + gross fitness
histogram as billing telemetry — not the raw signals.

---

## See also

- [`packages/decepticon/decepticon/core/evo_metaclaw.py`](../packages/decepticon/decepticon/core/evo_metaclaw.py) — the core module.
- [`tests/unit/core/test_evo_metaclaw.py`](../tests/unit/core/test_evo_metaclaw.py) — 14 unit tests.
- [`BLUEPRINT.md`](../BLUEPRINT.md) — the "two moats" framing at the top.
- [`docs/skills.md`](skills.md) — how skills load; unchanged by EvoMetaClaw.
