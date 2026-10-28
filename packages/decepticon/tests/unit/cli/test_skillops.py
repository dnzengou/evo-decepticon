"""Tests for ``decepticon-cli skillops``."""

from __future__ import annotations

import json
import random
from pathlib import Path

from decepticon.cli.__main__ import main as cli_main
from decepticon.cli.skillops import EXIT_NOOP, EXIT_OK
from decepticon.cli.skillops import main as skillops_main
from decepticon.core.evo_metaclaw import Genome, GenomeStore, Signal


def _seed_run_dir(path: Path) -> Path:
    """A linearly separable corpus plus one fit parent genome."""
    run_dir = path / "run"
    store = GenomeStore(run_dir)
    store.append_genome(Genome("good", "recon", ("a", "b"), "ph"))
    rng = random.Random(1)
    for i in range(20):
        store.append_signal(
            Signal("good", "recon", f"s{i % 4}", "nmap", 5.0 + rng.random(), 100, 0.5, True)
        )
        store.append_signal(
            Signal("bad", "recon", f"s{i % 4}", "brute", -1.0 + rng.random(), 8000, 8.0, False)
        )
    return run_dir


def test_retrain_persists_and_reports(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(["--run-dir", str(run_dir), "retrain"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "trained" in out
    assert (run_dir / "qgate.json").exists()


def test_retrain_json(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(["--run-dir", str(run_dir), "--json", "retrain"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["trained"] is True
    assert payload["persisted"] is True
    assert payload["weights"]["reward"] > 0.0


def test_retrain_dry_run_does_not_write(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(["--run-dir", str(run_dir), "--json", "retrain", "--dry-run"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["persisted"] is False
    assert not (run_dir / "qgate.json").exists()


def test_retrain_empty_is_noop(tmp_path: Path, capsys) -> None:
    rc = skillops_main(["--run-dir", str(tmp_path / "empty"), "retrain"])
    assert rc == EXIT_NOOP
    assert "no-op" in capsys.readouterr().out


def test_evolve_appends_child(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(
        ["--run-dir", str(run_dir), "--json", "evolve", "--top-k", "1", "--skill", "z"]
    )
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["children"]) == 1
    assert payload["children"][0]["parent_id"] == "good"
    assert payload["persisted"] is True


def test_evolve_dry_run(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    before = len(GenomeStore(run_dir).load_genomes())
    rc = skillops_main(
        ["--run-dir", str(run_dir), "evolve", "--top-k", "1", "--skill", "z", "--dry-run"]
    )
    assert rc == EXIT_OK
    assert len(GenomeStore(run_dir).load_genomes()) == before


def test_report_text(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(["--run-dir", str(run_dir), "report"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "fitness report" in out
    assert "top genomes:" in out


def test_dispatch_through_main(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = cli_main(["skillops", "--run-dir", str(run_dir), "report"])
    assert rc == EXIT_OK
    assert "fitness report" in capsys.readouterr().out


def test_promote_persists(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(["--run-dir", str(run_dir), "--json", "promote"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["persisted"] is True
    assert payload["champions"]["recon"]["genome"]["genome_id"] == "good"
    assert (run_dir / "champions.json").exists()


def test_promote_dry_run(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(["--run-dir", str(run_dir), "promote", "--dry-run"])
    assert rc == EXIT_OK
    assert not (run_dir / "champions.json").exists()


def test_promote_empty_is_noop(tmp_path: Path, capsys) -> None:
    rc = skillops_main(["--run-dir", str(tmp_path / "empty"), "promote"])
    assert rc == EXIT_NOOP
    assert "none" in capsys.readouterr().out


def test_grpo_exports(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(["--run-dir", str(run_dir), "--json", "grpo"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] > 0
    assert payload["persisted"] is True
    assert (run_dir / "grpo.jsonl").exists()


def test_grpo_dry_run(tmp_path: Path, capsys) -> None:
    run_dir = _seed_run_dir(tmp_path)
    rc = skillops_main(["--run-dir", str(run_dir), "grpo", "--dry-run"])
    assert rc == EXIT_OK
    assert not (run_dir / "grpo.jsonl").exists()
