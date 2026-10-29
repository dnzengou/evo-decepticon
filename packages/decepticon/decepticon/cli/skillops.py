"""``decepticon-cli skillops`` — offline EvoMetaClaw operations.

The nightly / on-demand half of the flywheel. Reads the trajectory log
under the run dir (``$EVO_METACLAW_RUN_DIR`` or ``evo-metaclaw-run/``),
and never leaves that tenancy:

  retrain   Refit the Q-gate weights over signals.jsonl and persist them.
  evolve    Spawn mutated children of the fittest genomes.
  promote   Select + persist the champion genome per role (runtime picks it).
  grpo      Export the signal corpus as GRPO training rows (grpo.jsonl).
  nightly   Run the full pass in order: retrain + evolve + promote + grpo.
  report    Print the billing-safe fitness telemetry (aggregates only).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from decepticon.core.evo_metaclaw import GenomeStore
from decepticon.core.evo_skillops import EvoSkillOps

EXIT_OK = 0
EXIT_NOOP = 1
EXIT_CONFIG = 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="decepticon-cli skillops",
        description="Offline EvoMetaClaw operations over the trajectory log.",
    )
    p.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Run dir holding genomes.jsonl / signals.jsonl. "
        "Defaults to $EVO_METACLAW_RUN_DIR or evo-metaclaw-run/.",
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    sub = p.add_subparsers(dest="command", required=True)

    retrain = sub.add_parser("retrain", help="Refit and persist Q-gate weights.")
    retrain.add_argument("--epochs", type=int, default=None, help="Gradient-descent epochs.")
    retrain.add_argument("--lr", type=float, default=None, help="Learning rate.")
    retrain.add_argument("--l2", type=float, default=None, help="L2 regularisation strength.")
    retrain.add_argument(
        "--dry-run", action="store_true", help="Report the fit without writing qgate.json."
    )

    evolve = sub.add_parser("evolve", help="Spawn mutated children of the fittest genomes.")
    evolve.add_argument("--top-k", type=int, default=3, help="How many fittest genomes spawn.")
    evolve.add_argument(
        "--min-signals", type=int, default=3, help="Min signals before a genome is eligible."
    )
    evolve.add_argument("--seed", type=int, default=0, help="RNG seed for reproducible mutation.")
    evolve.add_argument(
        "--skill",
        action="append",
        default=None,
        dest="skill_catalog",
        metavar="SKILL_ID",
        help="A skill id mutation may add (repeatable).",
    )
    evolve.add_argument(
        "--dry-run", action="store_true", help="List children without appending them."
    )

    promote = sub.add_parser("promote", help="Select + persist the champion genome per role.")
    promote.add_argument(
        "--min-signals", type=int, default=3, help="Min signals before a genome is eligible."
    )
    promote.add_argument(
        "--dry-run", action="store_true", help="Show champions without writing champions.json."
    )

    grpo = sub.add_parser("grpo", help="Export the signal corpus as GRPO training rows.")
    grpo.add_argument(
        "--group-size", type=int, default=None, help="Cap each context group to its last N signals."
    )
    grpo.add_argument(
        "--min-group", type=int, default=2, help="Skip contexts with fewer than N signals."
    )
    grpo.add_argument(
        "--dry-run", action="store_true", help="Count rows without writing grpo.jsonl."
    )

    nightly = sub.add_parser(
        "nightly", help="Run the full offline pass: retrain + evolve + promote + grpo."
    )
    nightly.add_argument("--top-k", type=int, default=3, help="How many fittest genomes spawn.")
    nightly.add_argument(
        "--min-signals", type=int, default=3, help="Min signals before a genome is eligible."
    )
    nightly.add_argument("--seed", type=int, default=0, help="RNG seed for reproducible mutation.")
    nightly.add_argument("--group-size", type=int, default=None, help="GRPO per-context cap.")
    nightly.add_argument(
        "--skill",
        action="append",
        default=None,
        dest="skill_catalog",
        metavar="SKILL_ID",
        help="A skill id mutation may add (repeatable).",
    )
    nightly.add_argument(
        "--dry-run", action="store_true", help="Run the pass without writing any artefact."
    )

    sub.add_parser("report", help="Print billing-safe fitness telemetry.")
    return p


def _emit(payload: dict, *, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(text, end="")


def _cmd_retrain(ops: EvoSkillOps, args: argparse.Namespace) -> int:
    kwargs = {}
    if args.epochs is not None:
        kwargs["epochs"] = args.epochs
    if args.lr is not None:
        kwargs["lr"] = args.lr
    if args.l2 is not None:
        kwargs["l2"] = args.l2
    result = ops.retrain(persist=not args.dry_run, **kwargs)
    payload = result.to_json()
    payload["persisted"] = result.trained and not args.dry_run
    lines = [
        f"retrain: {'trained' if result.trained else 'no-op (insufficient signal)'}",
        f"  signals:   {result.n_signals}",
        f"  base_rate: {result.base_rate:.3f}",
        f"  accuracy:  {result.accuracy:.3f}",
        f"  persisted: {'yes' if payload['persisted'] else 'no'}",
    ]
    if result.trained:
        lines.append("  weights:")
        lines.extend(f"    {k}: {v:+.6g}" for k, v in result.weights.items())
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return EXIT_OK if result.trained else EXIT_NOOP


def _cmd_evolve(ops: EvoSkillOps, args: argparse.Namespace) -> int:
    children = ops.evolve(
        top_k=args.top_k,
        min_signals=args.min_signals,
        skill_catalog=args.skill_catalog,
        seed=args.seed,
        persist=not args.dry_run,
    )
    payload = {
        "children": [c.to_json() for c in children],
        "persisted": bool(children) and not args.dry_run,
    }
    lines = [f"evolve: {len(children)} child genome(s)"]
    for c in children:
        lines.append(f"  {c.genome_id}  gen {c.generation}  <- {c.parent_id}  {list(c.skill_ids)}")
    if not children:
        lines.append("  (none — no eligible genome produced a novel mutation)")
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return EXIT_OK if children else EXIT_NOOP


def _cmd_report(ops: EvoSkillOps, args: argparse.Namespace) -> int:
    report = ops.report()
    payload = report.to_json()
    lines = [
        "fitness report",
        f"  signals:        {report.n_signals}",
        f"  genomes:        {report.n_genomes}",
        f"  max_generation: {report.max_generation}",
        f"  base_rate:      {report.base_rate:.3f}",
        "  histogram:",
    ]
    lines.extend(f"    {bucket}: {count}" for bucket, count in report.histogram.items())
    if report.top:
        lines.append("  top genomes:")
        for e in report.top:
            lines.append(
                f"    {e.genome_id}  fit {e.fitness:+.3f}  "
                f"succ {e.success_rate:.2f}  n {e.n_signals}  gen {e.generation}"
            )
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return EXIT_OK


def _cmd_promote(ops: EvoSkillOps, args: argparse.Namespace) -> int:
    champions = ops.promote(min_signals=args.min_signals, persist=not args.dry_run)
    payload = {
        "champions": {role: c.to_json() for role, c in champions.items()},
        "persisted": bool(champions) and not args.dry_run,
    }
    lines = [f"promote: {len(champions)} champion(s)"]
    for role, c in sorted(champions.items()):
        lines.append(
            f"  {role}: {c.genome.genome_id}  fit {c.fitness:+.3f}  "
            f"n {c.n_signals}  gen {c.genome.generation}  {list(c.genome.skill_ids)}"
        )
    if not champions:
        lines.append("  (none — no role has an eligible genome yet)")
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return EXIT_OK if champions else EXIT_NOOP


def _cmd_grpo(ops: EvoSkillOps, args: argparse.Namespace) -> int:
    rows = ops.export_grpo(
        group_size=args.group_size, min_group=args.min_group, persist=not args.dry_run
    )
    contexts = len({r["state_hash"] for r in rows})
    payload = {
        "rows": len(rows),
        "contexts": contexts,
        "persisted": bool(rows) and not args.dry_run,
    }
    text = (
        f"grpo: {len(rows)} row(s) across {contexts} context(s); "
        f"{'written' if payload['persisted'] else 'not written'}\n"
    )
    _emit(payload, as_json=args.json, text=text)
    return EXIT_OK if rows else EXIT_NOOP


def _cmd_nightly(ops: EvoSkillOps, args: argparse.Namespace) -> int:
    result = ops.nightly(
        top_k=args.top_k,
        min_signals=args.min_signals,
        skill_catalog=args.skill_catalog,
        seed=args.seed,
        group_size=args.group_size,
        persist=not args.dry_run,
    )
    payload = result.to_json()
    payload["persisted"] = not args.dry_run
    r = result.retrain
    lines = [
        f"nightly pass{' (dry-run)' if args.dry_run else ''}",
        f"  retrain:    {'trained' if r.trained else 'no-op'} "
        f"(n={r.n_signals}, acc={r.accuracy:.3f})",
        f"  evolved:    {result.n_children} child genome(s)",
        f"  promoted:   {result.n_champions} champion(s)",
        f"  grpo rows:  {result.n_grpo_rows}",
        f"  population: {result.report.n_genomes} genome(s), gen<={result.report.max_generation}",
    ]
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    # Non-zero only when there was nothing to do at all.
    did_something = r.trained or result.n_children or result.n_champions or result.n_grpo_rows
    return EXIT_OK if did_something else EXIT_NOOP


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    ops = EvoSkillOps(store=GenomeStore(args.run_dir))
    if args.command == "retrain":
        return _cmd_retrain(ops, args)
    if args.command == "evolve":
        return _cmd_evolve(ops, args)
    if args.command == "promote":
        return _cmd_promote(ops, args)
    if args.command == "grpo":
        return _cmd_grpo(ops, args)
    if args.command == "nightly":
        return _cmd_nightly(ops, args)
    if args.command == "report":
        return _cmd_report(ops, args)
    print(f"unknown skillops subcommand: {args.command}", file=sys.stderr)
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
