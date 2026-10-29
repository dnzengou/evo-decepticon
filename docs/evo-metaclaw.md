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

## EvoSkillOps — the offline half

Everything above is the *runtime* half: `propose` a genome, score it,
`record_signal`. That half deliberately only reads policy and appends
trajectory data — it never trains anything, because training inside an
active engagement is exactly the bug the "never break an active op"
discipline forbids.

The *offline* half is
[`evo_skillops.py`](../packages/decepticon/decepticon/core/evo_skillops.py)
— the nightly job (or on-demand `decepticon-cli skillops` run) that turns
the accumulated `signals.jsonl` back into a better policy and a fitter
population. Also stdlib-only.

| Operation            | What it does                                                                                     |
|----------------------|--------------------------------------------------------------------------------------------------|
| `retrain_qgate`      | Refit the linear Q-gate by logistic regression over the signal corpus, predicting `success` from `(reward, tokens_used, wallclock_s)`. Features are standardised for stable gradient descent, then the coefficients are mapped back to raw-feature space so they drop straight into `QGate` under the unchanged `score() >= threshold` rule. |
| `compute_fitness`    | Rank genomes by mean Q-gate score — reward *net of* token/wallclock cost, so a genome that wins cheaply outranks one that wins the same reward by burning the budget. |
| `evolve_population`  | Select the top-k eligible genomes (gated by a minimum signal count so one lucky turn can't seed a generation) and spawn skill-mutated children — drop / add / swap a skill — wiring `parent_id` + `generation` so lineage compounds. |
| `build_report`       | Aggregate the corpus into the billing-safe telemetry (`generation`, `signal_count`, fitness histogram). Aggregates only — never a raw signal. |

Composed by the `EvoSkillOps` façade over a `GenomeStore`:

- `retrain()` — load signals, refit, and **persist** the weights
  atomically to `qgate.json` in the run dir. A degenerate corpus (no
  signals, or a single label — nothing was ever rejected, so there's no
  boundary to fit) is a no-op that keeps the seed policy.
- `evolve()` — load genomes + signals, evolve, and append the children
  to the lineage log.
- `promote()` — select the fittest proven genome per role and persist the
  role→champion map to `champions.json`.
- `export_grpo()` — flatten the signal corpus into group-normalised GRPO
  rows (`grpo.jsonl`) for a downstream trainer.
- `nightly()` — run the whole pass (`retrain` → `evolve` → `promote` →
  `export_grpo` → `report`) and return a `NightlyResult` summary safe to
  log. The one operation an operator crons.
- `report()` — emit the fitness telemetry.

### Variation → selection → deployment

Evolution (`evolve`) is only the *variation* arm — it spawns children but
nothing makes the runtime use them. `promote` is the *selection* arm, and
`EvoMetaClaw.champion(role)` is *deployment*:

1. **Variation** — `evolve_population` mutates the fittest genomes into
   children (drop / add / swap a skill), bumping `generation`.
2. **Selection** — `select_champions` ranks every genome per role by mean
   Q-gate score and writes the winner (gated by a minimum signal count) to
   `champions.json`. Config only — the genome plus its fitness aggregate,
   never a raw signal.
3. **Deployment** — `EngagementLoop.propose(..., prefer_champion=True)`
   asks `EvoMetaClaw.champion(role)` first; if a champion exists and is not
   in breaker cooldown, the engagement runs it instead of a cold proposal.
   A missing or malformed champion falls through to the cold proposal —
   selection never blocks an engagement.

### Closing the loop

The runtime consumes what the offline job produces. On construction,
`EvoMetaClaw` autoloads `qgate.json` from the run dir when no gate is
injected — so the next engagement scores with the retrained policy
instead of the seed. A missing file falls back to the seed; a *corrupt*
file also falls back (wrapped in `try/except`, logged) rather than
blocking boot. Champions load the same defensive way. This is the moat's
compounding mechanism in code: a fork of the repo starts at
`qgate.json`-absent (generation 0); a fork of the tenancy inherits the
weights **and the champion genomes** its operators have trained.

### CLI

```
decepticon-cli skillops retrain [--epochs N --lr F --l2 F --dry-run]
decepticon-cli skillops evolve  [--top-k N --min-signals N --skill ID ... --seed N --dry-run]
decepticon-cli skillops promote [--min-signals N --dry-run]
decepticon-cli skillops grpo    [--group-size N --min-group N --dry-run]
decepticon-cli skillops nightly [--top-k N --min-signals N --skill ID ... --seed N --group-size N --dry-run]
decepticon-cli skillops report
```

All take `--run-dir` (defaults to `$EVO_METACLAW_RUN_DIR` or
`evo-metaclaw-run/`) and `--json`. `retrain` / `evolve` / `promote` /
`grpo` exit non-zero (no-op) when there was nothing to train on, no novel
mutation, no eligible champion, or no exportable context — handy as a
cron guard.

`nightly` is the whole pass in one command — `retrain`, then `evolve`,
`promote`, `grpo` in order (retrain first so the others score against the
freshly persisted gate). It's the single line an operator crons on their
tenancy, and exits non-zero only when the corpus yielded nothing at all:

```cron
# 03:17 nightly, inside the customer tenancy — signals never leave it
17 3 * * *  cd /srv/engagements && decepticon-cli skillops nightly --json >> evo-nightly.log 2>&1
```

---

## See also

- [`packages/decepticon/decepticon/core/evo_metaclaw.py`](../packages/decepticon/decepticon/core/evo_metaclaw.py) — the runtime core.
- [`packages/decepticon/decepticon/core/evo_skillops.py`](../packages/decepticon/decepticon/core/evo_skillops.py) — the offline ops layer.
- [`tests/unit/core/test_evo_metaclaw.py`](../tests/unit/core/test_evo_metaclaw.py) — runtime unit tests.
- [`tests/unit/core/test_evo_skillops.py`](../tests/unit/core/test_evo_skillops.py) — offline-ops unit tests.
- [`BLUEPRINT.md`](../BLUEPRINT.md) — the "two moats" framing at the top.
- [`docs/skills.md`](skills.md) — how skills load; unchanged by EvoMetaClaw.
