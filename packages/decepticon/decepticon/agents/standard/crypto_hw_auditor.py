"""CryptoHwAuditor Agent — cryptographic-hardware RNG + seed audit specialist.

Audits shipped cryptographic hardware (wallets, HSMs, TPMs, YubiKeys)
for the class of failure that took down Coldcard Mk2/Mk3 in 2021: a
silent TRNG-to-PRNG fallback that shrinks the seed key space to
something a laptop can enumerate.

Two audit lanes:

  - **RNG sample audit** — score captured RNG output for min-entropy,
    monobit bias, longest run, repeated-block ratio, catastrophic
    runs. Skill: `rng-entropy-audit`.
  - **Seed spec audit** — score BIP39 word count + passphrase bits for
    post-Grover strength. Recommend passphrase-bit deltas BEFORE
    recommending device replacement.

The agent NEVER accepts a target without explicit owner consent in
``plan/roe.json:machine_enforcement.crypto_hw.authorized``. It never
persists or transmits raw sample bytes or recovered mnemonics — only
SHA-256 hashes and derived metrics reach the KG.

Tool surface (all via bash for the OSS bootstrap):

  - python3 with helpers alongside SKILL.md under
    skills/standard/crypto-hw/<skill>/ (e.g. rng_entropy_score.py).
  - Standard filesystem + methodology-lookup tools.
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


_ROLE = "crypto_hw_auditor"
_RECURSION_LIMIT = 200
_SKILL_SOURCES: list[str] = ["/skills/standard/crypto-hw/", "/skills/shared/"]


def create_crypto_hw_auditor_agent(
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
    """Build the CryptoHwAuditor agent."""
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
    graph = create_crypto_hw_auditor_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="crypto_hw_auditor",
    description=(
        "Cryptographic-hardware RNG + seed audit specialist. Use for "
        "hardware wallet / HSM / TPM / YubiKey RNG audits (Coldcard-class "
        "TRNG->PRNG silent-fallback detection via min-entropy / monobit / "
        "repeated-block metrics) and BIP39 seed strength scoring (passphrase "
        "bits vs post-Grover target). Requires "
        "``machine_enforcement.crypto_hw.authorized`` in plan/roe.json; "
        "audits are limited to devices under owner control and never "
        "persist raw sample bytes or recovered mnemonics."
    ),
    factory=create_crypto_hw_auditor_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=87,
)
