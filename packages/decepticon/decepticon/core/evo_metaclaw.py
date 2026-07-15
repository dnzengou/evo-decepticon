"""EvoMetaClaw — the skill-library flywheel.

Four cooperating pieces, kept tiny and dependency-free so the core is
importable in any process (LangGraph node, CLI, test, offline retrain):

  - ``QGate``: linear gate on (reward, tokens_used, wallclock_s) that
    decides whether a candidate skill/prompt genome graduates. Weights
    are learned offline over ``signals.jsonl``; the runtime path only
    scores.
  - ``GRPOBuffer``: rolling buffer of (state_hash, action, reward,
    advantage) tuples for group-relative advantage computation. The
    ``sample_group`` API returns a fixed-size group with normalised
    advantages — the shape a downstream GRPO trainer consumes.
  - ``CircuitBreaker``: per-genome kill-switch. Trips when the last
    ``N`` signals show a failure rate above the threshold; a tripped
    genome is refused by ``QGate.accept()`` for a cooldown window.
  - ``GenomeStore``: append-only JSONL of ``Genome`` + ``Signal`` rows
    under ``run_dir/`` — the trajectory-data trail the moat rests on.

The public façade is :class:`EvoMetaClaw`, which composes all four and
exposes two hot-path calls: :meth:`propose` (given a role + context,
return a genome to try) and :meth:`record_signal` (given the outcome,
persist and update the buffer / breaker). Both are wrapped in the
engagement loop with ``try/except Exception``: the flywheel MUST NEVER
break an active engagement.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Default env-configurable run dir. Kept out of the repo tree; the
# .gitignore entry ``evo-metaclaw-run/`` covers the default.
_DEFAULT_RUN_DIR = Path(os.environ.get("EVO_METACLAW_RUN_DIR", "evo-metaclaw-run"))

# Q-gate weights — seed values, retrained nightly over signals.jsonl.
# Positive weight on reward, negative on cost. Anyone can override at
# construction time; the seed just gives a workable starting policy.
_DEFAULT_Q_WEIGHTS: dict[str, float] = {
    "reward": 1.0,
    "tokens_used": -1.0e-5,
    "wallclock_s": -1.0e-3,
    "bias": 0.0,
}
_DEFAULT_ACCEPT_THRESHOLD = 0.0

# GRPO buffer sizing — enough to compute a stable advantage without
# unbounded memory growth.
_DEFAULT_BUFFER_SIZE = 512
_DEFAULT_GROUP_SIZE = 16

# Circuit breaker defaults — trip if >= 60% of the last 5 signals for a
# genome failed; refuse the genome for 300 s after trip.
_DEFAULT_BREAKER_WINDOW = 5
_DEFAULT_BREAKER_FAIL_THRESHOLD = 0.6
_DEFAULT_BREAKER_COOLDOWN_S = 300.0


@dataclass(frozen=True)
class Genome:
    """A candidate skill/prompt configuration to try in the field."""

    genome_id: str
    role: str
    skill_ids: tuple[str, ...]
    prompt_hash: str
    parent_id: str | None = None
    generation: int = 0

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "skill_ids": list(self.skill_ids)}


@dataclass
class Signal:
    """One (state, action, outcome) row appended after each subagent turn."""

    genome_id: str
    role: str
    state_hash: str
    action: str
    reward: float
    tokens_used: int
    wallclock_s: float
    success: bool
    ts: float = field(default_factory=lambda: 0.0)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class QGate:
    """Linear gate scoring (reward, cost) tuples. Retrained offline."""

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        threshold: float = _DEFAULT_ACCEPT_THRESHOLD,
    ) -> None:
        self.weights = dict(_DEFAULT_Q_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self.threshold = threshold

    def score(self, signal: Signal) -> float:
        w = self.weights
        return (
            w["reward"] * signal.reward
            + w["tokens_used"] * signal.tokens_used
            + w["wallclock_s"] * signal.wallclock_s
            + w["bias"]
        )

    def accept(self, signal: Signal) -> bool:
        return self.score(signal) >= self.threshold


class GRPOBuffer:
    """Rolling buffer with group-relative advantage computation."""

    def __init__(
        self,
        capacity: int = _DEFAULT_BUFFER_SIZE,
        group_size: int = _DEFAULT_GROUP_SIZE,
    ) -> None:
        if capacity <= 0 or group_size <= 0:
            raise ValueError("capacity and group_size must be positive")
        self.capacity = capacity
        self.group_size = group_size
        self._buf: deque[Signal] = deque(maxlen=capacity)

    def push(self, signal: Signal) -> None:
        self._buf.append(signal)

    def __len__(self) -> int:
        return len(self._buf)

    def sample_group(self, state_hash: str | None = None) -> list[dict[str, Any]]:
        """Return a fixed-size group with group-normalised advantages.

        Groups are formed from the tail of the buffer, optionally
        filtered by ``state_hash`` so advantages are computed across
        actions from the same context — the GRPO ideal.
        """
        pool = list(self._buf)
        if state_hash is not None:
            pool = [s for s in pool if s.state_hash == state_hash]
        if not pool:
            return []
        group = pool[-self.group_size :]
        rewards = [s.reward for s in group]
        mean = sum(rewards) / len(rewards)
        # Population std, floored to avoid div-by-zero on identical rewards.
        var = sum((r - mean) ** 2 for r in rewards) / len(rewards)
        std = var**0.5 or 1.0
        return [
            {
                "genome_id": s.genome_id,
                "action": s.action,
                "reward": s.reward,
                "advantage": (s.reward - mean) / std,
            }
            for s in group
        ]


class CircuitBreaker:
    """Per-genome kill-switch on rolling failure rate."""

    def __init__(
        self,
        window: int = _DEFAULT_BREAKER_WINDOW,
        fail_threshold: float = _DEFAULT_BREAKER_FAIL_THRESHOLD,
        cooldown_s: float = _DEFAULT_BREAKER_COOLDOWN_S,
        clock: Any = None,
    ) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        if not 0.0 < fail_threshold <= 1.0:
            raise ValueError("fail_threshold must be in (0, 1]")
        self.window = window
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self._clock = clock or time.monotonic
        self._history: dict[str, deque[bool]] = {}
        self._tripped_at: dict[str, float] = {}

    def record(self, genome_id: str, success: bool) -> None:
        hist = self._history.setdefault(genome_id, deque(maxlen=self.window))
        hist.append(success)
        if len(hist) >= self.window:
            fail_rate = 1.0 - (sum(hist) / len(hist))
            if fail_rate >= self.fail_threshold:
                self._tripped_at[genome_id] = self._clock()

    def is_tripped(self, genome_id: str) -> bool:
        trip_ts = self._tripped_at.get(genome_id)
        if trip_ts is None:
            return False
        if self._clock() - trip_ts >= self.cooldown_s:
            # Cooldown elapsed — clear the trip and give the genome a
            # fresh window. History is intentionally kept so the second
            # trip requires proving it again.
            self._tripped_at.pop(genome_id, None)
            self._history.pop(genome_id, None)
            return False
        return True


class GenomeStore:
    """Append-only JSONL persistence for genomes + signals."""

    def __init__(self, run_dir: Path | str | None = None) -> None:
        self.run_dir = Path(run_dir) if run_dir else _DEFAULT_RUN_DIR
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.genomes_path = self.run_dir / "genomes.jsonl"
        self.signals_path = self.run_dir / "signals.jsonl"

    def append_genome(self, genome: Genome) -> None:
        with self.genomes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(genome.to_json(), sort_keys=True) + "\n")

    def append_signal(self, signal: Signal) -> None:
        with self.signals_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(signal.to_json(), sort_keys=True) + "\n")

    def load_signals(self) -> list[Signal]:
        if not self.signals_path.exists():
            return []
        out: list[Signal] = []
        with self.signals_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(Signal(**json.loads(line)))
        return out


def hash_state(payload: Any) -> str:
    """Stable 12-char hash for state / prompt fingerprints."""
    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.blake2s(raw, digest_size=6).hexdigest()


class EvoMetaClaw:
    """Public façade — compose Q-gate, GRPO buffer, breaker, and store."""

    def __init__(
        self,
        run_dir: Path | str | None = None,
        q_gate: QGate | None = None,
        buffer: GRPOBuffer | None = None,
        breaker: CircuitBreaker | None = None,
        store: GenomeStore | None = None,
        clock: Any = None,
    ) -> None:
        self.q_gate = q_gate or QGate()
        self.buffer = buffer or GRPOBuffer()
        self.breaker = breaker or CircuitBreaker(clock=clock)
        self.store = store or GenomeStore(run_dir)
        self._clock = clock or time.time

    def propose(
        self,
        role: str,
        skill_ids: tuple[str, ...],
        prompt: str,
        parent_id: str | None = None,
        generation: int = 0,
    ) -> Genome:
        """Return a fresh genome, persisted before it is handed out.

        The genome_id is a content-hash of ``(role, skill_ids, prompt)``
        so identical proposals collapse to the same lineage row.
        """
        prompt_hash = hash_state(prompt)
        genome_id = hash_state(
            {"role": role, "skills": list(skill_ids), "prompt_hash": prompt_hash}
        )
        genome = Genome(
            genome_id=genome_id,
            role=role,
            skill_ids=tuple(skill_ids),
            prompt_hash=prompt_hash,
            parent_id=parent_id,
            generation=generation,
        )
        self.store.append_genome(genome)
        return genome

    def is_available(self, genome_id: str) -> bool:
        """False if the breaker has this genome in cooldown."""
        return not self.breaker.is_tripped(genome_id)

    def record_signal(
        self,
        genome: Genome,
        state: Any,
        action: str,
        reward: float,
        tokens_used: int = 0,
        wallclock_s: float = 0.0,
        success: bool | None = None,
    ) -> Signal:
        """Persist a signal, update the buffer + breaker, return it.

        ``success`` defaults to ``reward > 0`` — the caller can override
        when the domain distinguishes zero-reward-but-successful (e.g.
        a negative-finding recon step).
        """
        signal = Signal(
            genome_id=genome.genome_id,
            role=genome.role,
            state_hash=hash_state(state),
            action=action,
            reward=reward,
            tokens_used=tokens_used,
            wallclock_s=wallclock_s,
            success=success if success is not None else reward > 0,
            ts=self._clock(),
        )
        self.store.append_signal(signal)
        self.buffer.push(signal)
        self.breaker.record(genome.genome_id, signal.success)
        return signal


__all__ = [
    "CircuitBreaker",
    "EvoMetaClaw",
    "GRPOBuffer",
    "Genome",
    "GenomeStore",
    "QGate",
    "Signal",
    "hash_state",
]
