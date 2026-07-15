"""Unit tests for the EvoMetaClaw moat core.

14 tests covering Q-gate scoring/threshold, GRPO buffer group
sampling / normalisation, circuit-breaker trip/cooldown, GenomeStore
JSONL round-trip, EvoMetaClaw propose/record integration, and the
engagement-loop exception swallow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decepticon.core.engagement_loop import EngagementLoop
from decepticon.core.evo_metaclaw import (
    CircuitBreaker,
    EvoMetaClaw,
    GRPOBuffer,
    Genome,
    GenomeStore,
    QGate,
    Signal,
    hash_state,
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


class TestQGate:
    def test_default_accepts_positive_reward(self) -> None:
        gate = QGate()
        assert gate.accept(_sig(reward=10.0, tokens_used=0, wallclock_s=0.0))

    def test_rejects_negative_score(self) -> None:
        gate = QGate()
        assert not gate.accept(_sig(reward=-1.0, tokens_used=100000, wallclock_s=10.0))

    def test_custom_weights_override(self) -> None:
        gate = QGate(weights={"reward": 0.0, "bias": 5.0}, threshold=1.0)
        assert gate.score(_sig(reward=999.0)) == pytest.approx(
            5.0 + 0.0 * 999.0 - 1e-5 * 100 - 1e-3 * 0.5
        )
        assert gate.accept(_sig(reward=0.0, tokens_used=0, wallclock_s=0.0))


class TestGRPOBuffer:
    def test_push_and_len(self) -> None:
        buf = GRPOBuffer(capacity=4)
        for _ in range(5):
            buf.push(_sig())
        assert len(buf) == 4  # deque maxlen enforced

    def test_sample_group_normalises_advantages(self) -> None:
        buf = GRPOBuffer(capacity=10, group_size=4)
        for r in [1.0, 2.0, 3.0, 4.0]:
            buf.push(_sig(reward=r, state_hash="ctx-A"))
        group = buf.sample_group("ctx-A")
        assert len(group) == 4
        # Mean of advantages must be ~0, variance ~1 (population std).
        advs = [row["advantage"] for row in group]
        assert sum(advs) == pytest.approx(0.0, abs=1e-9)

    def test_state_hash_filter(self) -> None:
        buf = GRPOBuffer(capacity=10, group_size=8)
        buf.push(_sig(reward=1.0, state_hash="ctx-A"))
        buf.push(_sig(reward=1.0, state_hash="ctx-B"))
        buf.push(_sig(reward=1.0, state_hash="ctx-A"))
        assert len(buf.sample_group("ctx-A")) == 2
        assert len(buf.sample_group("ctx-B")) == 1
        assert buf.sample_group("no-such-ctx") == []

    def test_invalid_config_rejected(self) -> None:
        with pytest.raises(ValueError):
            GRPOBuffer(capacity=0)
        with pytest.raises(ValueError):
            GRPOBuffer(group_size=0)


class TestCircuitBreaker:
    def test_trips_on_high_failure_rate(self) -> None:
        clock = [0.0]
        br = CircuitBreaker(window=4, fail_threshold=0.5, cooldown_s=10.0, clock=lambda: clock[0])
        for _ in range(4):
            br.record("g1", success=False)
        assert br.is_tripped("g1")

    def test_does_not_trip_below_threshold(self) -> None:
        clock = [0.0]
        br = CircuitBreaker(window=4, fail_threshold=0.75, cooldown_s=10.0, clock=lambda: clock[0])
        # 2/4 failures → 50% fail rate, below 75% threshold
        br.record("g1", success=False)
        br.record("g1", success=True)
        br.record("g1", success=False)
        br.record("g1", success=True)
        assert not br.is_tripped("g1")

    def test_cooldown_expires(self) -> None:
        clock = [0.0]
        br = CircuitBreaker(window=2, fail_threshold=0.5, cooldown_s=5.0, clock=lambda: clock[0])
        br.record("g1", success=False)
        br.record("g1", success=False)
        assert br.is_tripped("g1")
        clock[0] = 6.0
        assert not br.is_tripped("g1")


class TestGenomeStore:
    def test_appends_and_reloads_signals(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path)
        s1 = _sig(genome_id="g1", ts=1.0)
        s2 = _sig(genome_id="g2", ts=2.0)
        store.append_signal(s1)
        store.append_signal(s2)
        loaded = store.load_signals()
        assert [s.genome_id for s in loaded] == ["g1", "g2"]

    def test_appends_genome_jsonl(self, tmp_path: Path) -> None:
        store = GenomeStore(tmp_path)
        g = Genome(genome_id="gX", role="recon", skill_ids=("s1", "s2"), prompt_hash="ph")
        store.append_genome(g)
        lines = store.genomes_path.read_text().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["genome_id"] == "gX"
        assert parsed["skill_ids"] == ["s1", "s2"]


class TestEvoMetaClaw:
    def test_propose_persists_genome(self, tmp_path: Path) -> None:
        evo = EvoMetaClaw(run_dir=tmp_path)
        g = evo.propose(role="recon", skill_ids=("nmap-tcp",), prompt="hi")
        assert g.role == "recon"
        # Genome is content-addressed — same inputs yield the same id.
        g2 = evo.propose(role="recon", skill_ids=("nmap-tcp",), prompt="hi")
        assert g.genome_id == g2.genome_id

    def test_record_signal_updates_buffer_and_breaker(self, tmp_path: Path) -> None:
        clock = [0.0]
        evo = EvoMetaClaw(run_dir=tmp_path, clock=lambda: clock[0])
        # Rebuild breaker with the injected clock so cooldown is deterministic.
        evo.breaker = CircuitBreaker(window=2, fail_threshold=0.5, cooldown_s=5.0, clock=lambda: clock[0])
        g = evo.propose(role="recon", skill_ids=("nmap-tcp",), prompt="hi")
        evo.record_signal(g, state={"k": 1}, action="nmap", reward=-1.0, success=False)
        evo.record_signal(g, state={"k": 1}, action="nmap", reward=-1.0, success=False)
        assert not evo.is_available(g.genome_id)  # tripped
        assert len(evo.buffer) == 2


class TestEngagementLoop:
    def test_step_swallows_record_signal_exceptions(self, tmp_path: Path) -> None:
        class Boom:
            def is_available(self, _: str) -> bool:
                return True

            def propose(self, **_: object) -> Genome:
                return Genome(genome_id="g", role="r", skill_ids=(), prompt_hash="p")

            def record_signal(self, **_: object) -> Signal:
                raise RuntimeError("evo blew up")

        loop = EngagementLoop(evo=Boom())  # type: ignore[arg-type]
        g = loop.propose(role="recon", skill_ids=(), prompt="")
        assert g is not None
        assert loop.step(g, state={}, action="a", reward=1.0) is None


class TestHashState:
    def test_string_and_dict_stable(self) -> None:
        assert hash_state("abc") == hash_state("abc")
        assert hash_state({"b": 1, "a": 2}) == hash_state({"a": 2, "b": 1})
        assert hash_state("abc") != hash_state("abd")
