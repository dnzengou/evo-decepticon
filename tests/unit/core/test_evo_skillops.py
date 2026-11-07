"""Unit tests for the EvoSkillOps offline-ops layer.

Covers Q-gate retraining (learned separation, degenerate corpora,
raw-space weight mapping), genome fitness ranking, skill mutation,
population evolution (selection, min-signals gate, dedup, determinism),
the fitness report aggregates, and the EvoSkillOps store façade including
the runtime weight-autoload round-trip through EvoMetaClaw.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from decepticon.core.evo_metaclaw import (
    EvoMetaClaw,
    Genome,
    GenomeStore,
    QGate,
    Signal,
)
from decepticon.core.evo_skillops import (
    EvoSkillOps,
    build_report,
    check_freshness,
    compute_fitness,
    evolve_population,
    mutate_genome,
    retrain_qgate,
)


def _sig(
    genome_id: str = "g1",
    role: str = "recon",
    state_hash: str = "s1",
    action: str = "nmap",
    reward: float = 1.0,
    tokens_used: int = 100,
    wallclock_s: float = 0.5,
    success: bool = True,
    ts: float = 0.0,
) -> Signal:
    return Signal(
        genome_id=genome_id,
        role=role,
        state_hash=state_hash,
        action=action,
        reward=reward,
        tokens_used=tokens_used,
        wallclock_s=wallclock_s,
        success=success,
        ts=ts,
    )


def _labelled_corpus(n: int = 30) -> list[Signal]:
    """A linearly separable corpus: cheap high-reward wins, expensive
    negative-reward losses."""
    rng = random.Random(1)
    sigs: list[Signal] = []
    for i in range(n):
        sigs.append(
            _sig(
                genome_id="good",
                state_hash=f"s{i % 4}",
                reward=5.0 + rng.random(),
                tokens_used=rng.randint(50, 150),
                wallclock_s=0.5,
                success=True,
            )
        )
    for i in range(n):
        sigs.append(
            _sig(
                genome_id="bad",
                action="brute",
                state_hash=f"s{i % 4}",
                reward=-1.0 + rng.random(),
                tokens_used=rng.randint(5000, 9000),
                wallclock_s=8.0,
                success=False,
            )
        )
    return sigs


class TestRetrainQGate:
    def test_empty_corpus_keeps_seed_untrained(self) -> None:
        result = retrain_qgate([])
        assert result.trained is False
        assert result.n_signals == 0
        assert result.weights["reward"] == 1.0

    def test_single_label_corpus_is_untrained(self) -> None:
        # Nothing was ever rejected → no boundary to fit.
        result = retrain_qgate([_sig(success=True) for _ in range(10)])
        assert result.trained is False
        assert result.base_rate == 1.0

    def test_learns_to_separate(self) -> None:
        result = retrain_qgate(_labelled_corpus())
        assert result.trained is True
        assert result.accuracy == pytest.approx(1.0)
        # Reward should push the score up, cost features down.
        assert result.weights["reward"] > 0.0
        assert result.weights["tokens_used"] < 0.0
        assert result.weights["wallclock_s"] < 0.0

    def test_weights_map_to_raw_feature_space(self) -> None:
        # The learned gate must score the raw signals it was trained on
        # with the same accept rule QGate already uses.
        sigs = _labelled_corpus()
        result = retrain_qgate(sigs)
        gate = QGate(weights=result.weights, threshold=result.threshold)
        assert gate.accept(_sig(reward=6.0, tokens_used=80, wallclock_s=0.5))
        assert not gate.accept(_sig(reward=-1.0, tokens_used=8000, wallclock_s=8.0))

    def test_zero_variance_feature_is_safe(self) -> None:
        # wallclock_s identical across every row → std floored, no div-by-zero.
        sigs = [_sig(reward=3.0, wallclock_s=1.0, success=True) for _ in range(5)]
        sigs += [_sig(reward=-3.0, wallclock_s=1.0, success=False) for _ in range(5)]
        result = retrain_qgate(sigs)
        assert result.trained is True
        assert all(v == v for v in result.weights.values())  # no NaN


class TestComputeFitness:
    def test_ranks_by_mean_score(self) -> None:
        sigs = _labelled_corpus()
        genomes = [
            Genome("good", "recon", ("passive-recon",), "ph"),
            Genome("bad", "recon", ("brute",), "ph2"),
        ]
        entries = compute_fitness(genomes, sigs, QGate())
        assert [e.genome_id for e in entries] == ["good", "bad"]
        assert entries[0].fitness > entries[1].fitness
        assert entries[0].success_rate == 1.0
        assert entries[1].success_rate == 0.0

    def test_genome_without_signals_is_omitted(self) -> None:
        genomes = [Genome("idle", "recon", ("x",), "ph")]
        assert compute_fitness(genomes, [], QGate()) == []

    def test_signals_without_lineage_still_report(self) -> None:
        # Older run dir: signals exist, no genome row — generation defaults 0.
        entries = compute_fitness([], [_sig(genome_id="orphan")], QGate())
        assert len(entries) == 1
        assert entries[0].generation == 0


class TestMutateGenome:
    def test_drop_skill(self) -> None:
        parent = Genome("p", "recon", ("a", "b", "c"), "ph", generation=2)
        child = mutate_genome(parent, random.Random(0))
        assert child is not None
        assert child.parent_id == "p"
        assert child.generation == 3
        assert set(child.skill_ids) < {"a", "b", "c"}

    def test_add_from_catalog(self) -> None:
        parent = Genome("p", "recon", ("a",), "ph")
        child = mutate_genome(parent, random.Random(3), skill_catalog=["z"])
        assert child is not None
        assert "z" in child.skill_ids

    def test_single_skill_no_catalog_cannot_mutate(self) -> None:
        parent = Genome("p", "recon", ("only",), "ph")
        assert mutate_genome(parent, random.Random(0)) is None

    def test_child_id_matches_propose_hash(self) -> None:
        # A mutated child's id must equal the one EvoMetaClaw.propose would
        # mint for the same config, so lineage stays consistent.
        from decepticon.core.evo_metaclaw import hash_state

        parent = Genome("p", "recon", ("a", "b"), "phx", generation=0)
        child = mutate_genome(parent, random.Random(0))
        assert child is not None
        expected = hash_state(
            {"role": "recon", "skills": list(child.skill_ids), "prompt_hash": "phx"}
        )
        assert child.genome_id == expected


class TestEvolvePopulation:
    def test_promotes_top_k_fittest(self) -> None:
        sigs = _labelled_corpus()
        genomes = [
            Genome("good", "recon", ("a", "b"), "ph"),
            Genome("bad", "recon", ("brute",), "ph2"),
        ]
        children = evolve_population(
            genomes, sigs, gate=QGate(), top_k=1, min_signals=3, skill_catalog=["z"], seed=0
        )
        # top_k=1 → only the fittest genome ("good") spawns.
        assert len(children) == 1
        assert children[0].parent_id == "good"
        assert children[0].generation == 1

    def test_min_signals_gate(self) -> None:
        genomes = [Genome("good", "recon", ("a", "b"), "ph")]
        sigs = [_sig(genome_id="good") for _ in range(2)]
        children = evolve_population(genomes, sigs, min_signals=5, skill_catalog=["z"])
        assert children == []

    def test_dedups_against_existing(self) -> None:
        # The only reachable mutation reproduces an existing genome id.
        parent = Genome("p", "recon", ("a", "b"), "ph")
        # Child from dropping "a" → skills ("b",); pre-seed that id.
        from decepticon.core.evo_metaclaw import hash_state

        existing_id = hash_state({"role": "recon", "skills": ["b"], "prompt_hash": "ph"})
        existing = Genome(existing_id, "recon", ("b",), "ph", generation=1)
        other_id = hash_state({"role": "recon", "skills": ["a"], "prompt_hash": "ph"})
        other = Genome(other_id, "recon", ("a",), "ph", generation=1)
        sigs = [_sig(genome_id="p") for _ in range(5)]
        # Force drop-only mutations by giving no catalog; both possible
        # children ("a",)/("b",) are already in the population.
        children = evolve_population(
            [parent, existing, other], sigs, top_k=1, min_signals=3, seed=0
        )
        assert children == []

    def test_deterministic_in_seed(self) -> None:
        sigs = _labelled_corpus()
        genomes = [Genome("good", "recon", ("a", "b"), "ph")]
        a = evolve_population(genomes, sigs, skill_catalog=["z", "y"], seed=42)
        b = evolve_population(genomes, sigs, skill_catalog=["z", "y"], seed=42)
        assert [c.genome_id for c in a] == [c.genome_id for c in b]


class TestBuildReport:
    def test_aggregates_without_leaking_signals(self) -> None:
        sigs = _labelled_corpus()
        genomes = [
            Genome("good", "recon", ("a",), "ph", generation=1),
            Genome("bad", "recon", ("b",), "ph2"),
        ]
        report = build_report(genomes, sigs, QGate())
        payload = report.to_json()
        assert payload["n_signals"] == 60
        assert payload["n_genomes"] == 2
        assert payload["max_generation"] == 1
        assert payload["base_rate"] == pytest.approx(0.5)
        assert payload["histogram"]  # non-empty
        # Telemetry carries aggregates only — no raw signal fields.
        assert "state_hash" not in str(payload)


class TestEvoSkillOpsFacade:
    def test_retrain_persists_weights(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        for s in _labelled_corpus():
            store.append_signal(s)
        ops = EvoSkillOps(store=store)
        result = ops.retrain()
        assert result.trained is True
        payload = store.load_weights()
        assert payload is not None
        assert payload["weights"]["reward"] > 0.0
        assert payload["meta"]["n_signals"] == 60

    def test_untrained_retrain_does_not_persist(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        store.append_signal(_sig(success=True))
        ops = EvoSkillOps(store=store)
        result = ops.retrain()
        assert result.trained is False
        assert store.load_weights() is None

    def test_evolve_appends_children(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        parent = Genome("good", "recon", ("a", "b"), "ph")
        store.append_genome(parent)
        for s in _labelled_corpus():
            if s.genome_id == "good":
                store.append_signal(s)
        ops = EvoSkillOps(store=store)
        children = ops.evolve(top_k=1, min_signals=3, skill_catalog=["z"], seed=0)
        assert len(children) == 1
        reloaded = {g.genome_id for g in store.load_genomes()}
        assert children[0].genome_id in reloaded

    def test_report_round_trip(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        store.append_genome(Genome("good", "recon", ("a",), "ph"))
        for s in _labelled_corpus():
            store.append_signal(s)
        report = EvoSkillOps(store=store).report()
        assert report.n_signals == 60

    def test_runtime_loads_retrained_weights(self, tmp_path: Path) -> None:
        # The whole point of the flywheel: retrain → persist → the next
        # runtime boot scores with the learned policy, not the seed.
        run_dir = tmp_path / "run"
        store = GenomeStore(run_dir)
        for s in _labelled_corpus():
            store.append_signal(s)
        trained = EvoSkillOps(store=store).retrain().weights

        evo = EvoMetaClaw(run_dir=run_dir)
        assert evo.q_gate.weights["reward"] == pytest.approx(trained["reward"])
        assert evo.q_gate.weights["reward"] != 1.0  # moved off the seed

    def test_runtime_survives_corrupt_weights(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "qgate.json").write_text("{ not json", encoding="utf-8")
        # Must fall back to seed weights, never raise on boot.
        evo = EvoMetaClaw(run_dir=run_dir)
        assert evo.q_gate.weights["reward"] == 1.0

    def test_from_evo_shares_store(self, tmp_path: Path) -> None:
        evo = EvoMetaClaw(run_dir=tmp_path / "run")
        ops = EvoSkillOps.from_evo(evo)
        assert ops.store is evo.store


_HOUR = 3600.0


class TestCheckFreshness:
    def test_never_trained_no_signals_is_benign(self) -> None:
        # A genuine generation-0 run: no weights, nothing to train on.
        status = check_freshness(None, [], now=1000.0)
        assert status.state == "never_trained"
        assert status.is_stale is False
        assert status.n_signals_total == 0

    def test_never_trained_with_signals_alarms(self) -> None:
        # Signals exist but the gate is still on the seed policy — the
        # moat is not compounding.
        sigs = [_sig(ts=10.0), _sig(ts=20.0)]
        status = check_freshness(None, sigs, now=1000.0)
        assert status.state == "never_trained"
        assert status.is_stale is True
        assert status.n_signals_since_train == 2

    def test_fresh_when_recent_and_no_backlog(self) -> None:
        payload = {"weights": {}, "threshold": 0.0, "trained_at": 1000.0}
        sigs = [_sig(ts=500.0), _sig(ts=900.0)]  # all predate the retrain
        status = check_freshness(payload, sigs, now=1000.0 + _HOUR)
        assert status.state == "fresh"
        assert status.is_stale is False
        assert status.n_signals_since_train == 0
        assert status.age_s == pytest.approx(_HOUR)

    def test_stale_by_age_requires_new_signals(self) -> None:
        payload = {"trained_at": 0.0}
        # One new signal after the retrain, and it's been well past max age.
        sigs = [_sig(ts=10.0)]
        now = 50 * _HOUR
        fresh = check_freshness(payload, [], now=now, max_age_s=36 * _HOUR)
        stale = check_freshness(payload, sigs, now=now, max_age_s=36 * _HOUR)
        # No new signals → quiet period, not stale, even though it's old.
        assert fresh.is_stale is False
        # New signal + past max age → stale.
        assert stale.is_stale is True
        assert any("ago" in r for r in stale.reasons)

    def test_stale_by_backlog(self) -> None:
        payload = {"trained_at": 0.0}
        sigs = [_sig(ts=float(i + 1)) for i in range(5)]
        now = 1 * _HOUR  # well within max age
        status = check_freshness(payload, sigs, now=now, max_new_signals=5)
        assert status.is_stale is True
        assert status.n_signals_since_train == 5
        assert any("since the last retrain" in r for r in status.reasons)

    def test_signals_since_train_counts_strictly_after(self) -> None:
        payload = {"trained_at": 100.0}
        sigs = [_sig(ts=100.0), _sig(ts=101.0), _sig(ts=50.0)]
        status = check_freshness(payload, sigs, now=200.0)
        # ts == trained_at is not "since"; only ts > trained_at counts.
        assert status.n_signals_since_train == 1


class TestEvoSkillOpsFreshness:
    def test_round_trip_stamps_and_reads_fresh(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        for s in _labelled_corpus():
            store.append_signal(s)
        ops = EvoSkillOps(store=store)
        result = ops.retrain()  # persists weights with a trained_at stamp
        assert result.trained is True
        status = ops.freshness()
        assert status.trained_at is not None
        # All corpus signals have ts=0.0 (predate the just-now retrain), so
        # there is no backlog and the gate is fresh.
        assert status.is_stale is False
        assert status.state == "fresh"

    def test_never_trained_facade(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        for s in _labelled_corpus():
            store.append_signal(s)
        status = EvoSkillOps(store=store).freshness()
        assert status.state == "never_trained"
        assert status.is_stale is True

    def test_corrupt_weights_treated_as_never_trained(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "qgate.json").write_text("{ not json", encoding="utf-8")
        # Must not raise — the alarm can never itself break the nightly job.
        status = EvoSkillOps(run_dir=run_dir).freshness()
        assert status.state == "never_trained"


class TestEvoSkillOpsRun:
    def test_run_trains_evolves_reports_and_is_fresh(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        # A fit parent with enough signals to be eligible for evolution.
        store.append_genome(Genome("good", "recon", ("a", "b"), "ph"))
        for s in _labelled_corpus():
            store.append_signal(s)
        result = EvoSkillOps(store=store).run(top_k=1, min_signals=3, skill_catalog=["z"])
        assert result.dry_run is False
        assert result.retrain.trained is True
        assert (tmp_path / "run" / "qgate.json").exists()  # weights persisted
        # The corpus signals predate the just-now retrain → no backlog.
        assert result.freshness.is_stale is False
        assert result.report.n_signals == 60

    def test_run_evolves_on_the_retrained_gate(self, tmp_path: Path) -> None:
        # Ordering guarantee: the weights file exists (retrain persisted)
        # before evolve selected its parents.
        store = GenomeStore(tmp_path / "run")
        store.append_genome(Genome("good", "recon", ("a", "b"), "ph"))
        for s in _labelled_corpus():
            store.append_signal(s)
        result = EvoSkillOps(store=store).run(top_k=1, min_signals=3, skill_catalog=["z"])
        # 'good' is the only eligible parent and a novel '--skill z' mutation
        # is available, so exactly one child is spawned and persisted.
        assert len(result.children) == 1
        assert result.children[0].parent_id == "good"
        assert result.children[0].generation == 1
        persisted = {g.genome_id for g in store.load_genomes()}
        assert result.children[0].genome_id in persisted

    def test_run_dry_run_persists_nothing(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        store = GenomeStore(run_dir)
        store.append_genome(Genome("good", "recon", ("a", "b"), "ph"))
        for s in _labelled_corpus():
            store.append_signal(s)
        before = len(store.load_genomes())
        result = EvoSkillOps(store=store).run(
            top_k=1, min_signals=3, skill_catalog=["z"], persist=False
        )
        assert result.dry_run is True
        assert not (run_dir / "qgate.json").exists()
        assert len(GenomeStore(run_dir).load_genomes()) == before

    def test_run_on_empty_corpus_is_never_trained(self, tmp_path: Path) -> None:
        result = EvoSkillOps(run_dir=tmp_path / "run").run()
        assert result.retrain.trained is False
        assert result.children == []
        # No signals at all → benign generation-0 state, not an alarm.
        assert result.freshness.state == "never_trained"
        assert result.freshness.is_stale is False
