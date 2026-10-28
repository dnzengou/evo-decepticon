"""EvoSkillOps — the offline operations layer for the EvoMetaClaw moat.

Where :mod:`decepticon.core.evo_metaclaw` is the *runtime* half of the
flywheel (propose a genome, score it, record a signal — never break the
engagement), EvoSkillOps is the *offline* half the docs promise: the
nightly job that turns the accumulated ``signals.jsonl`` back into a
better policy and a fitter genome population.

Three operations, all stdlib-only so the retrain job runs anywhere the
core imports:

  - :func:`retrain_qgate` — fit the linear Q-gate weights to the signal
    corpus by logistic regression, predicting ``success`` from
    ``(reward, tokens_used, wallclock_s)``. Features are standardised for
    stable gradient descent, then the coefficients are mapped back to the
    raw-feature space so they drop straight into :class:`QGate` with the
    same ``score() >= threshold`` decision rule.
  - :func:`compute_fitness` — rank every genome by the mean Q-gate score
    its signals earned (so fitness respects the learned reward/cost
    trade-off, not just raw reward).
  - :func:`evolve_population` — select the top genomes and spawn mutated
    children (drop / add / swap a skill), bumping ``generation`` and
    wiring ``parent_id`` so the lineage compounds.

The :class:`EvoSkillOps` façade wires these to a :class:`GenomeStore`:
load the corpus, retrain, persist the weights for the runtime to pick
up, evolve the population, and emit the fitness telemetry the dashboard
(and billing) reads — ``generation``, ``signal_count``, fitness
histogram — without ever shipping a raw signal off the tenancy.
"""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decepticon.core.evo_metaclaw import (
    _DEFAULT_ACCEPT_THRESHOLD,
    _DEFAULT_Q_WEIGHTS,
    EvoMetaClaw,
    Genome,
    GenomeStore,
    QGate,
    Signal,
    hash_state,
)

log = logging.getLogger(__name__)

# The raw signal fields the linear gate scores, in weight-key order.
_FEATURES: tuple[str, ...] = ("reward", "tokens_used", "wallclock_s")

# Retrain defaults — a few hundred epochs of plain GD over a standardised
# design matrix converges the small linear model well within a nightly job.
_DEFAULT_EPOCHS = 400
_DEFAULT_LR = 0.5
_DEFAULT_L2 = 1e-3

# Evolution defaults — promote the top few genomes, but only once a genome
# has enough signals that its fitness is not a single lucky turn.
_DEFAULT_TOP_K = 3
_DEFAULT_MIN_SIGNALS = 3


def _sigmoid(z: float) -> float:
    # Clamp to dodge math.exp overflow on confident predictions.
    if z < -60.0:
        return 0.0
    if z > 60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


@dataclass(frozen=True)
class RetrainResult:
    """Outcome of a Q-gate retrain over the signal corpus."""

    weights: dict[str, float]
    threshold: float
    n_signals: int
    epochs: int
    accuracy: float
    base_rate: float
    trained: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "weights": self.weights,
            "threshold": self.threshold,
            "n_signals": self.n_signals,
            "epochs": self.epochs,
            "accuracy": self.accuracy,
            "base_rate": self.base_rate,
            "trained": self.trained,
        }


def retrain_qgate(
    signals: Sequence[Signal],
    *,
    epochs: int = _DEFAULT_EPOCHS,
    lr: float = _DEFAULT_LR,
    l2: float = _DEFAULT_L2,
    threshold: float = _DEFAULT_ACCEPT_THRESHOLD,
) -> RetrainResult:
    """Fit Q-gate weights to ``signals`` by logistic regression.

    The model predicts ``P(success)`` from the raw gate features. Because
    ``sigmoid(score) >= 0.5`` iff ``score >= 0``, the learned coefficients
    reuse the gate's existing ``score() >= threshold`` rule unchanged at
    ``threshold = 0`` — the retrain just moves the weights, not the API.

    Returns the seed policy untouched (``trained=False``) when there is
    nothing to learn from: no signals, or every signal shares one label.
    """
    n = len(signals)
    seed_weights = dict(_DEFAULT_Q_WEIGHTS)
    if n == 0:
        return RetrainResult(seed_weights, threshold, 0, 0, 0.0, 0.0, trained=False)

    labels = [1.0 if s.success else 0.0 for s in signals]
    base_rate = sum(labels) / n

    rows = [[float(getattr(s, f)) for f in _FEATURES] for s in signals]
    # Standardise each feature; the raw columns span wildly different
    # scales (reward ~O(1), tokens ~O(1e4)) which stalls plain GD.
    means = [sum(col) / n for col in zip(*rows, strict=True)]
    stds: list[float] = []
    for j, col in enumerate(zip(*rows, strict=True)):
        var = sum((v - means[j]) ** 2 for v in col) / n
        stds.append(math.sqrt(var) or 1.0)
    design = [[(row[j] - means[j]) / stds[j] for j in range(len(_FEATURES))] for row in rows]

    # A single-label corpus has no decision boundary to fit — the gate
    # can't learn "accept when X" if nothing was ever rejected (or vice
    # versa). Keep the seed policy and report the degenerate base rate.
    if base_rate in (0.0, 1.0):
        return RetrainResult(seed_weights, threshold, n, 0, base_rate, base_rate, trained=False)

    wz = [0.0] * len(_FEATURES)
    bias = 0.0
    for _ in range(epochs):
        grad_w = [0.0] * len(_FEATURES)
        grad_b = 0.0
        for i, z in enumerate(design):
            pred = _sigmoid(bias + sum(wz[j] * z[j] for j in range(len(_FEATURES))))
            err = pred - labels[i]
            for j in range(len(_FEATURES)):
                grad_w[j] += err * z[j]
            grad_b += err
        for j in range(len(_FEATURES)):
            grad_w[j] = grad_w[j] / n + l2 * wz[j]
            wz[j] -= lr * grad_w[j]
        bias -= lr * (grad_b / n)

    # Map standardised coefficients back to raw-feature space so QGate can
    # score raw signals directly: w_raw = wz / std, and the bias absorbs
    # the shift from every re-centred feature.
    weights = {f: wz[j] / stds[j] for j, f in enumerate(_FEATURES)}
    weights["bias"] = bias - sum(wz[j] * means[j] / stds[j] for j in range(len(_FEATURES)))

    gate = QGate(weights=weights, threshold=threshold)
    correct = sum(1 for i, s in enumerate(signals) if gate.accept(s) == (labels[i] == 1.0))
    return RetrainResult(weights, threshold, n, epochs, correct / n, base_rate, trained=True)


@dataclass(frozen=True)
class FitnessEntry:
    """One genome's aggregate performance over the signal corpus."""

    genome_id: str
    role: str
    generation: int
    n_signals: int
    mean_reward: float
    mean_score: float
    success_rate: float
    fitness: float

    def to_json(self) -> dict[str, Any]:
        return {
            "genome_id": self.genome_id,
            "role": self.role,
            "generation": self.generation,
            "n_signals": self.n_signals,
            "mean_reward": self.mean_reward,
            "mean_score": self.mean_score,
            "success_rate": self.success_rate,
            "fitness": self.fitness,
        }


def compute_fitness(
    genomes: Sequence[Genome],
    signals: Sequence[Signal],
    gate: QGate | None = None,
) -> list[FitnessEntry]:
    """Rank genomes by mean Q-gate score, highest fitness first.

    Fitness is the mean gate score (not raw reward) so a genome that wins
    reward cheaply outranks one that wins the same reward by burning the
    token budget. Genomes with no recorded signals are omitted — there is
    nothing to select on. Signals whose genome is absent from ``genomes``
    still contribute a synthetic lineage row so a store with only signals
    (an older run dir) still reports.
    """
    gate = gate or QGate()
    by_id: dict[str, list[Signal]] = {}
    for s in signals:
        by_id.setdefault(s.genome_id, []).append(s)

    gen_of = {g.genome_id: g.generation for g in genomes}
    out: list[FitnessEntry] = []
    for genome_id, group in by_id.items():
        k = len(group)
        scores = [gate.score(s) for s in group]
        mean_score = sum(scores) / k
        out.append(
            FitnessEntry(
                genome_id=genome_id,
                role=group[0].role,
                generation=gen_of.get(genome_id, 0),
                n_signals=k,
                mean_reward=sum(s.reward for s in group) / k,
                mean_score=mean_score,
                success_rate=sum(1 for s in group if s.success) / k,
                fitness=mean_score,
            )
        )
    # Sort by fitness desc, then genome_id for a deterministic tie-break.
    out.sort(key=lambda e: (-e.fitness, e.genome_id))
    return out


def mutate_genome(
    parent: Genome,
    rng: random.Random,
    skill_catalog: Sequence[str] | None = None,
) -> Genome | None:
    """Spawn one mutated child genome, or ``None`` if no change is possible.

    Mutation operates on the skill set — the one part of a genome this
    offline pass can vary without re-authoring a prompt. The child's
    ``genome_id`` is the same content hash :meth:`EvoMetaClaw.propose`
    computes, so a mutation that happens to reproduce an existing config
    collapses onto that lineage instead of forking a duplicate.
    """
    skills = list(parent.skill_ids)
    addable = [s for s in (skill_catalog or ()) if s not in skills]

    ops: list[str] = []
    if len(skills) > 1:
        ops.append("drop")
    if addable:
        ops.append("add")
    if addable and skills:
        ops.append("swap")
    if not ops:
        return None

    op = rng.choice(ops)
    if op == "drop":
        skills.pop(rng.randrange(len(skills)))
    elif op == "add":
        skills.append(rng.choice(addable))
    else:  # swap
        skills[rng.randrange(len(skills))] = rng.choice(addable)

    child_skills = tuple(skills)
    genome_id = hash_state(
        {"role": parent.role, "skills": list(child_skills), "prompt_hash": parent.prompt_hash}
    )
    if genome_id == parent.genome_id:
        return None
    return Genome(
        genome_id=genome_id,
        role=parent.role,
        skill_ids=child_skills,
        prompt_hash=parent.prompt_hash,
        parent_id=parent.genome_id,
        generation=parent.generation + 1,
    )


def evolve_population(
    genomes: Sequence[Genome],
    signals: Sequence[Signal],
    *,
    gate: QGate | None = None,
    top_k: int = _DEFAULT_TOP_K,
    min_signals: int = _DEFAULT_MIN_SIGNALS,
    skill_catalog: Sequence[str] | None = None,
    seed: int = 0,
) -> list[Genome]:
    """Select the fittest genomes and return their mutated children.

    A genome is eligible only once it has at least ``min_signals`` recorded
    turns, so a single lucky result can't seed the next generation. The top
    ``top_k`` eligible genomes each spawn one child. Children are
    deterministic in ``seed`` and deduplicated against the existing
    population (and each other) by ``genome_id``.
    """
    fitness = compute_fitness(genomes, signals, gate)
    by_id = {g.genome_id: g for g in genomes}
    known = set(by_id)
    rng = random.Random(seed)

    children: list[Genome] = []
    promoted = 0
    for entry in fitness:
        if promoted >= top_k:
            break
        if entry.n_signals < min_signals:
            continue
        parent = by_id.get(entry.genome_id)
        if parent is None:
            # Signals reference a genome we have no lineage row for; we
            # can't mutate a skill set we never recorded. Skip it.
            continue
        promoted += 1
        child = mutate_genome(parent, rng, skill_catalog)
        if child is None or child.genome_id in known:
            continue
        known.add(child.genome_id)
        children.append(child)
    return children


@dataclass
class FitnessReport:
    """The non-sensitive telemetry the dashboard and billing read.

    Deliberately holds aggregates only — generation, counts, a fitness
    histogram — never a raw signal. This is the shape that can leave the
    tenancy as billing telemetry; ``signals.jsonl`` never does.
    """

    n_signals: int
    n_genomes: int
    max_generation: int
    base_rate: float
    histogram: dict[str, int]
    top: list[FitnessEntry] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "n_signals": self.n_signals,
            "n_genomes": self.n_genomes,
            "max_generation": self.max_generation,
            "base_rate": self.base_rate,
            "histogram": self.histogram,
            "top": [e.to_json() for e in self.top],
        }


def _fitness_histogram(entries: Sequence[FitnessEntry], buckets: int = 5) -> dict[str, int]:
    """Bucket genome fitness into ``buckets`` equal-width bins."""
    if not entries:
        return {}
    values = [e.fitness for e in entries]
    lo, hi = min(values), max(values)
    if hi == lo:
        return {f"{lo:.3f}": len(values)}
    width = (hi - lo) / buckets
    hist: dict[str, int] = {}
    for v in values:
        idx = min(int((v - lo) / width), buckets - 1)
        label = f"{lo + idx * width:.3f}..{lo + (idx + 1) * width:.3f}"
        hist[label] = hist.get(label, 0) + 1
    return hist


def build_report(
    genomes: Sequence[Genome],
    signals: Sequence[Signal],
    gate: QGate | None = None,
    top_n: int = 10,
) -> FitnessReport:
    """Aggregate the corpus into billing-safe telemetry."""
    entries = compute_fitness(genomes, signals, gate)
    labels = [s.success for s in signals]
    base_rate = (sum(1 for s in labels if s) / len(labels)) if labels else 0.0
    max_gen = max((e.generation for e in entries), default=0)
    return FitnessReport(
        n_signals=len(signals),
        n_genomes=len(entries),
        max_generation=max_gen,
        base_rate=base_rate,
        histogram=_fitness_histogram(entries),
        top=entries[:top_n],
    )


@dataclass(frozen=True)
class Champion:
    """The fittest proven genome for one role — the runtime's default pick."""

    genome: Genome
    fitness: float
    n_signals: int

    def to_json(self) -> dict[str, Any]:
        return {
            "genome": self.genome.to_json(),
            "fitness": self.fitness,
            "n_signals": self.n_signals,
        }


def select_champions(
    genomes: Sequence[Genome],
    signals: Sequence[Signal],
    gate: QGate | None = None,
    *,
    min_signals: int = _DEFAULT_MIN_SIGNALS,
) -> dict[str, Champion]:
    """Pick the fittest eligible genome per role.

    Eligibility mirrors evolution's: a genome must have at least
    ``min_signals`` recorded turns and a lineage row we can rebuild (the
    runtime needs its skills + prompt hash to redeploy it). Roles with no
    eligible genome are simply absent — the runtime proposes cold for them.
    """
    by_id = {g.genome_id: g for g in genomes}
    champions: dict[str, Champion] = {}
    for entry in compute_fitness(genomes, signals, gate):
        if entry.n_signals < min_signals:
            continue
        genome = by_id.get(entry.genome_id)
        if genome is None:
            continue
        # compute_fitness is sorted fittest-first, so the first eligible
        # genome seen for a role is its champion.
        if entry.role not in champions:
            champions[entry.role] = Champion(genome, entry.fitness, entry.n_signals)
    return champions


def export_grpo(
    signals: Sequence[Signal],
    *,
    group_size: int | None = None,
    min_group: int = 2,
) -> list[dict[str, Any]]:
    """Flatten the signal corpus into GRPO training rows.

    Groups signals by ``state_hash`` — the GRPO ideal of comparing actions
    taken from the same context — and emits one row per signal with the
    group-normalised advantage ``(reward - mean) / std``. Contexts with
    fewer than ``min_group`` signals are skipped (no meaningful spread to
    normalise against); ``group_size`` optionally caps each group to its
    most recent N signals. Rows are ordered by context then insertion, so
    a given corpus always exports identically.
    """
    by_state: dict[str, list[Signal]] = {}
    for s in signals:
        by_state.setdefault(s.state_hash, []).append(s)

    rows: list[dict[str, Any]] = []
    for state_hash in sorted(by_state):
        group = by_state[state_hash]
        if group_size is not None:
            group = group[-group_size:]
        if len(group) < min_group:
            continue
        rewards = [s.reward for s in group]
        mean = sum(rewards) / len(rewards)
        var = sum((r - mean) ** 2 for r in rewards) / len(rewards)
        std = var**0.5 or 1.0
        for s in group:
            rows.append(
                {
                    "state_hash": state_hash,
                    "genome_id": s.genome_id,
                    "role": s.role,
                    "action": s.action,
                    "reward": s.reward,
                    "advantage": (s.reward - mean) / std,
                    "group_size": len(group),
                }
            )
    return rows


class EvoSkillOps:
    """Offline-ops façade: retrain the gate, evolve the population, report.

    Wraps a :class:`GenomeStore`. Every method loads the corpus fresh so
    the façade holds no stale state between nightly runs, and the destructive
    side (persisting weights, appending children) is opt-in via ``persist``.
    """

    def __init__(
        self,
        run_dir: Path | str | None = None,
        store: GenomeStore | None = None,
    ) -> None:
        self.store = store or GenomeStore(run_dir)

    def load_gate(self) -> QGate:
        """The gate the *runtime* would use — persisted weights, or seed."""
        payload = self.store.load_weights()
        if not payload:
            return QGate()
        return QGate(
            weights=payload.get("weights"),
            threshold=payload.get("threshold", _DEFAULT_ACCEPT_THRESHOLD),
        )

    def retrain(
        self,
        *,
        epochs: int = _DEFAULT_EPOCHS,
        lr: float = _DEFAULT_LR,
        l2: float = _DEFAULT_L2,
        persist: bool = True,
    ) -> RetrainResult:
        """Retrain the gate over the whole signal log; persist on success."""
        signals = self.store.load_signals()
        result = retrain_qgate(signals, epochs=epochs, lr=lr, l2=l2)
        if persist and result.trained:
            self.store.save_weights(
                result.weights,
                result.threshold,
                meta={
                    "n_signals": result.n_signals,
                    "accuracy": result.accuracy,
                    "base_rate": result.base_rate,
                },
            )
        return result

    def evolve(
        self,
        *,
        top_k: int = _DEFAULT_TOP_K,
        min_signals: int = _DEFAULT_MIN_SIGNALS,
        skill_catalog: Sequence[str] | None = None,
        seed: int = 0,
        persist: bool = True,
    ) -> list[Genome]:
        """Evolve the population; append children to the lineage log."""
        genomes = self.store.load_genomes()
        signals = self.store.load_signals()
        children = evolve_population(
            genomes,
            signals,
            gate=self.load_gate(),
            top_k=top_k,
            min_signals=min_signals,
            skill_catalog=skill_catalog,
            seed=seed,
        )
        if persist:
            for child in children:
                self.store.append_genome(child)
        return children

    def report(self, top_n: int = 10) -> FitnessReport:
        """Emit billing-safe fitness telemetry over the current corpus."""
        return build_report(
            self.store.load_genomes(),
            self.store.load_signals(),
            gate=self.load_gate(),
            top_n=top_n,
        )

    def promote(
        self,
        *,
        min_signals: int = _DEFAULT_MIN_SIGNALS,
        persist: bool = True,
    ) -> dict[str, Champion]:
        """Select the champion genome per role; persist the map for runtime.

        This is the deployment arm: once a genome has proven itself over the
        corpus, write it to ``champions.json`` so the next engagement reaches
        for it instead of a cold proposal.
        """
        champions = select_champions(
            self.store.load_genomes(),
            self.store.load_signals(),
            gate=self.load_gate(),
            min_signals=min_signals,
        )
        if persist and champions:
            self.store.save_champions({r: c.to_json() for r, c in champions.items()})
        return champions

    def export_grpo(
        self,
        *,
        group_size: int | None = None,
        min_group: int = 2,
        persist: bool = True,
    ) -> list[dict[str, Any]]:
        """Export the signal corpus as GRPO training rows; write grpo.jsonl."""
        rows = export_grpo(self.store.load_signals(), group_size=group_size, min_group=min_group)
        if persist and rows:
            self.store.save_grpo(rows)
        return rows

    @classmethod
    def from_evo(cls, evo: EvoMetaClaw) -> EvoSkillOps:
        """Build an ops façade over the same store a runtime instance uses."""
        return cls(store=evo.store)


__all__ = [
    "Champion",
    "EvoSkillOps",
    "FitnessEntry",
    "FitnessReport",
    "RetrainResult",
    "build_report",
    "compute_fitness",
    "evolve_population",
    "export_grpo",
    "mutate_genome",
    "retrain_qgate",
    "select_champions",
]
