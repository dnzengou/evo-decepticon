"""Engagement loop — the outer driver that steps through one Red Team
engagement (recon → exploit → post-exploit → C2) and emits EvoMetaClaw
signals after each subagent turn.

The signal-capture hook is exception-swallowed at every call site. The
flywheel is a moat, not a critical path: any bug in genome scoring,
buffer maintenance, or JSONL append must NOT break an active
engagement. Failures are logged and the loop marches on.

Public API
----------
``EngagementLoop(evo=...)`` — inject an ``EvoMetaClaw`` instance (any
duck-typed object with ``propose`` / ``record_signal`` / ``is_available``
also works — the loop only calls those three).

``loop.step(...)`` — one subagent turn. Returns the ``Signal`` that
would have been recorded (or ``None`` on exception). Callers hand back
the turn's action / reward / usage; the loop owns the persistence side.

``loop.run(...)`` — thin convenience: iterate ``step`` over a list of
turn callables. Kept small on purpose — engagement orchestration lives
in LangGraph nodes, this module only wires the flywheel in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from decepticon.core.evo_metaclaw import EvoMetaClaw, Genome, Signal

log = logging.getLogger(__name__)


class EngagementLoop:
    """Driver that owns EvoMetaClaw signal capture for one engagement."""

    def __init__(self, evo: EvoMetaClaw | None = None) -> None:
        self.evo = evo or EvoMetaClaw()

    def propose(
        self,
        role: str,
        skill_ids: tuple[str, ...],
        prompt: str,
        parent_id: str | None = None,
        generation: int = 0,
    ) -> Genome | None:
        """Propose a genome — never raises. Returns ``None`` on failure."""
        try:
            genome = self.evo.propose(
                role=role,
                skill_ids=skill_ids,
                prompt=prompt,
                parent_id=parent_id,
                generation=generation,
            )
        except Exception:
            log.exception("evo_metaclaw.propose failed; skipping proposal")
            return None
        if not self.evo.is_available(genome.genome_id):
            log.info("evo_metaclaw genome %s in cooldown; caller must retry", genome.genome_id)
            return None
        return genome

    def step(
        self,
        genome: Genome | None,
        state: Any,
        action: str,
        reward: float,
        tokens_used: int = 0,
        wallclock_s: float = 0.0,
        success: bool | None = None,
    ) -> Signal | None:
        """Record one subagent turn — never raises. Returns ``None`` on failure."""
        if genome is None:
            return None
        try:
            return self.evo.record_signal(
                genome=genome,
                state=state,
                action=action,
                reward=reward,
                tokens_used=tokens_used,
                wallclock_s=wallclock_s,
                success=success,
            )
        except Exception:
            log.exception("evo_metaclaw.record_signal failed; engagement continues")
            return None

    def run(
        self,
        turns: list[Callable[[], dict[str, Any]]],
        genome: Genome | None,
    ) -> list[Signal | None]:
        """Iterate a list of turn thunks; each returns a dict of
        (``state``, ``action``, ``reward``, ``tokens_used``,
        ``wallclock_s``, ``success``) to feed :meth:`step`.

        A turn thunk raising is logged and skipped — same
        exception-swallow discipline as :meth:`step` itself.
        """
        out: list[Signal | None] = []
        for turn in turns:
            try:
                result = turn()
            except Exception:
                log.exception("engagement turn raised; continuing")
                out.append(None)
                continue
            out.append(self.step(genome=genome, **result))
        return out


__all__ = ["EngagementLoop"]
