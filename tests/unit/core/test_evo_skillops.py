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

from decepticon.core.engagement_loop import EngagementLoop
from decepticon.core.evo_metaclaw import (
    EvoMetaClaw,
    Genome,
    GenomeStore,
    QGate,
    Signal,
)
from decepticon.core.evo_skillops import (
    Champion,
    EvoSkillOps,
    build_report,
    compute_fitness,
    evolve_population,
    export_grpo,
    mutate_genome,
    retrain_qgate,
    select_champions,
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


class TestSelectChampions:
    def test_picks_fittest_per_role(self) -> None:
        sigs = _labelled_corpus()
        genomes = [
            Genome("good", "recon", ("a", "b"), "ph"),
            Genome("bad", "recon", ("brute",), "ph2"),
        ]
        champs = select_champions(genomes, sigs, QGate(), min_signals=3)
        assert set(champs) == {"recon"}
        assert champs["recon"].genome.genome_id == "good"
        assert champs["recon"].n_signals == 30

    def test_min_signals_gate(self) -> None:
        genomes = [Genome("good", "recon", ("a",), "ph")]
        sigs = [_sig(genome_id="good") for _ in range(2)]
        assert select_champions(genomes, sigs, min_signals=5) == {}

    def test_one_champion_per_role(self) -> None:
        genomes = [
            Genome("r1", "recon", ("a",), "p1"),
            Genome("r2", "recon", ("b",), "p2"),
            Genome("e1", "exploit", ("c",), "p3"),
        ]
        sigs = [_sig(genome_id="r1", role="recon", reward=1.0) for _ in range(4)]
        sigs += [_sig(genome_id="r2", role="recon", reward=9.0) for _ in range(4)]
        sigs += [_sig(genome_id="e1", role="exploit", reward=2.0) for _ in range(4)]
        champs = select_champions(genomes, sigs, QGate(), min_signals=3)
        assert champs["recon"].genome.genome_id == "r2"  # higher reward wins
        assert champs["exploit"].genome.genome_id == "e1"

    def test_signals_without_lineage_are_skipped(self) -> None:
        # A champion must be redeployable — no genome row, no champion.
        champs = select_champions([], [_sig(genome_id="orphan") for _ in range(5)], QGate())
        assert champs == {}


class TestExportGrpo:
    def test_groups_by_state_hash(self) -> None:
        sigs = [
            _sig(state_hash="s1", reward=1.0),
            _sig(state_hash="s1", reward=3.0),
            _sig(state_hash="s2", reward=5.0),
            _sig(state_hash="s2", reward=5.0),
        ]
        rows = export_grpo(sigs)
        assert len(rows) == 4
        # Advantages within each context sum to ~0 (mean-centred).
        by_ctx: dict[str, list[float]] = {}
        for r in rows:
            by_ctx.setdefault(r["state_hash"], []).append(r["advantage"])
        for advs in by_ctx.values():
            assert sum(advs) == pytest.approx(0.0, abs=1e-9)

    def test_min_group_skips_thin_contexts(self) -> None:
        sigs = [_sig(state_hash="lonely", reward=1.0)]
        assert export_grpo(sigs, min_group=2) == []

    def test_group_size_caps_tail(self) -> None:
        sigs = [_sig(state_hash="s1", reward=float(i)) for i in range(10)]
        rows = export_grpo(sigs, group_size=3)
        assert len(rows) == 3
        assert all(r["group_size"] == 3 for r in rows)
        # Only the last three rewards (7, 8, 9) survive the cap.
        assert sorted(r["reward"] for r in rows) == [7.0, 8.0, 9.0]

    def test_identical_rewards_safe(self) -> None:
        # std floored to 1.0 — no div-by-zero, advantages all 0.
        rows = export_grpo([_sig(state_hash="s1", reward=2.0) for _ in range(3)])
        assert all(r["advantage"] == 0.0 for r in rows)

    def test_deterministic(self) -> None:
        sigs = _labelled_corpus()
        assert export_grpo(sigs) == export_grpo(sigs)


class TestChampionRuntime:
    def _promote(self, tmp_path: Path) -> GenomeStore:
        store = GenomeStore(tmp_path / "run")
        store.append_genome(Genome("good", "recon", ("a", "b"), "ph"))
        for s in _labelled_corpus():
            if s.genome_id == "good":
                store.append_signal(s)
        EvoSkillOps(store=store).promote(min_signals=3)
        return store

    def test_promote_persists_and_runtime_loads(self, tmp_path: Path) -> None:
        self._promote(tmp_path)
        evo = EvoMetaClaw(run_dir=tmp_path / "run")
        champ = evo.champion("recon")
        assert champ is not None
        assert champ.genome_id == "good"
        assert champ.skill_ids == ("a", "b")

    def test_champion_absent_role_is_none(self, tmp_path: Path) -> None:
        self._promote(tmp_path)
        evo = EvoMetaClaw(run_dir=tmp_path / "run")
        assert evo.champion("exploit") is None

    def test_runtime_survives_corrupt_champions(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "champions.json").write_text("{ not json", encoding="utf-8")
        evo = EvoMetaClaw(run_dir=run_dir)
        assert evo.champion("recon") is None  # never raises

    def test_malformed_champion_entry_is_none(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        store = GenomeStore(run_dir)
        store.save_champions({"recon": {"fitness": 1.0}})  # no "genome" key
        evo = EvoMetaClaw(run_dir=run_dir)
        assert evo.champion("recon") is None

    def test_loop_prefers_champion(self, tmp_path: Path) -> None:
        store = self._promote(tmp_path)
        loop = EngagementLoop(evo=EvoMetaClaw(store=store))
        picked = loop.propose("recon", ("cold",), "coldprompt", prefer_champion=True)
        assert picked is not None
        assert picked.genome_id == "good"

    def test_loop_falls_back_when_no_champion(self, tmp_path: Path) -> None:
        store = self._promote(tmp_path)
        loop = EngagementLoop(evo=EvoMetaClaw(store=store))
        picked = loop.propose("exploit", ("x",), "p", prefer_champion=True)
        assert picked is not None
        assert picked.genome_id != "good"  # cold proposal for the new role

    def test_loop_ignores_champion_by_default(self, tmp_path: Path) -> None:
        store = self._promote(tmp_path)
        loop = EngagementLoop(evo=EvoMetaClaw(store=store))
        picked = loop.propose("recon", ("cold",), "coldprompt")
        assert picked is not None
        assert picked.genome_id != "good"  # cold proposal, champion not consulted


class TestFacadePromoteExport:
    def test_promote_dry_run_does_not_write(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        store.append_genome(Genome("good", "recon", ("a",), "ph"))
        for s in _labelled_corpus():
            if s.genome_id == "good":
                store.append_signal(s)
        champs = EvoSkillOps(store=store).promote(persist=False)
        assert "recon" in champs
        assert store.load_champions() == {}

    def test_export_grpo_persists(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        for s in _labelled_corpus():
            store.append_signal(s)
        rows = EvoSkillOps(store=store).export_grpo()
        assert rows
        assert store.grpo_path.exists()
        written = store.grpo_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(written) == len(rows)

    def test_export_grpo_dry_run(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path / "run")
        for s in _labelled_corpus():
            store.append_signal(s)
        EvoSkillOps(store=store).export_grpo(persist=False)
        assert not store.grpo_path.exists()

    def test_champion_to_json_round_trip(self) -> None:
        g = Genome("x", "recon", ("a", "b"), "ph", generation=2)
        c = Champion(g, fitness=1.5, n_signals=7)
        payload = c.to_json()
        assert payload["genome"]["skill_ids"] == ["a", "b"]
        assert payload["fitness"] == 1.5
        assert payload["n_signals"] == 7
