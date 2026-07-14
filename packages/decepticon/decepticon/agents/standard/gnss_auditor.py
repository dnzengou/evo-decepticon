"""GnssAuditor Agent - GNSS/PNT authentication & resilience specialist.

Audits GNSS receivers, PNT chains, and space-segment auth stacks against
the current threat model:

  - **TESLA / OSNMA / HAS PQC audit** — hash-length + tag-length gap
    versus Grover, root-signature quantum readiness (DSM-PKR), cadence
    and clock-alignment sanity.
  - **Jamming / spoofing resilience** — TTLOF (time-to-loss-of-fix),
    walk-off resistance under authorized spoofing (SDR-driven,
    authorization-gated), receiver AGC/CN0/RAIM telemetry review.

The agent NEVER transmits without an explicit authorization block in
``plan/roe.json`` under ``machine_enforcement.rf.gnss``. Physical-layer
work defaults to record-and-review; TX only inside an anechoic chamber
or licensed test cage.

Tool surface (all via bash for the OSS bootstrap):

  - gnss-sdr / RTKLIB / SDRAngel for record + replay analysis.
  - gnss-sim / GPS-SDR-SIM (authorization-gated) for controlled spoof.
  - python3 with helpers under skills/standard/gnss/*/scripts/.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from decepticon.agents._benchmark_mode import benchmark_skill_sources
from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.llm import LLMFactory
from decepticon.tools.bash import BASH_TOOLS
from decepticon.tools.bash.bash import set_sandbox
from decepticon.tools.references.tools import methodology_lookup
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        methodology_lookup,
        *BASH_TOOLS,
    ]
}


_ROLE = "gnss_auditor"
_RECURSION_LIMIT = 200
_SKILL_SOURCES: list[str] = ["/skills/standard/gnss/", "/skills/shared/"]


def create_gnss_auditor_agent(
    *,
    backend: Any = None,
    llm: Any = None,
    fallback_models: list | None = None,
    sandbox: Any = None,
    tools: list[Any] | None = None,
    middleware: list[Any] | None = None,
    system_prompt: str | None = None,
    recursion_limit: int | None = None,
):
    """Build the GnssAuditor agent."""
    if llm is None or fallback_models is None:
        factory = LLMFactory()
        if llm is None:
            llm = factory.get_model(_ROLE)
        if fallback_models is None:
            fallback_models = factory.get_fallback_models(_ROLE)

    if sandbox is None:
        sandbox = build_sandbox_backend()
    set_sandbox(sandbox)

    if backend is None:
        backend = make_agent_backend(sandbox)

    if tools is None:
        tools = build_tools(role=_ROLE, standard_tools=_STANDARD_TOOLS)
    if middleware is None:
        middleware = build_middleware(
            role=_ROLE,
            skill_sources=[*_SKILL_SOURCES, *benchmark_skill_sources()],
            backend=backend,
            llm=llm,
            fallback_models=fallback_models,
            sandbox=sandbox,
        )
    if system_prompt is None:
        system_prompt = load_prompt(_ROLE, shared=["bash"])

    return create_agent(
        llm,
        system_prompt=system_prompt,
        tools=tools,
        middleware=middleware,
        name=_ROLE,
    ).with_config(
        {
            "recursion_limit": recursion_limit or _RECURSION_LIMIT,
            "callbacks": load_plugin_callbacks(role=_ROLE, backend=backend),
        }
    )


# Module-level graph for LangGraph Platform (langgraph serve)
if is_bundle_enabled("standard"):
    graph = create_gnss_auditor_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="gnss_auditor",
    description=(
        "GNSS / PNT authentication + resilience specialist. Use for "
        "TESLA / OSNMA / Galileo HAS PQC audits (hash + tag Grover gap, "
        "DSM-PKR root-signature quantum readiness), jamming / spoofing "
        "resilience (TTLOF, walk-off), and receiver AGC/CN0/RAIM review. "
        "Requires ``machine_enforcement.rf.gnss.authorized`` in "
        "plan/roe.json for any TX; default posture is record-and-review."
    ),
    factory=create_gnss_auditor_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=88,
)
