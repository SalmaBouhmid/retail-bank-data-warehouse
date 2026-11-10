"""Tests of the dbt launcher bank_dw.run_dbt (dbt itself is not run here)."""

from __future__ import annotations

import csv
from pathlib import Path

from bank_dw import run_dbt


def test_dbt_command_points_to_transform_folder():
    command = run_dbt.dbt_command(["build"], "dbt")
    assert command[:2] == ["dbt", "build"]
    assert command[command.index("--project-dir") + 1] == str(run_dbt.PROJECT_DIR)
    assert command[command.index("--profiles-dir") + 1] == str(run_dbt.PROJECT_DIR)


def test_find_dbt_prefers_the_project_venv(tmp_path, monkeypatch):
    monkeypatch.delenv("DBT_EXECUTABLE", raising=False)
    dbt = tmp_path / ".venv" / "Scripts" / "dbt.exe"
    dbt.parent.mkdir(parents=True)
    dbt.touch()
    assert run_dbt.find_dbt(tmp_path) == str(dbt)


def test_find_dbt_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DBT_EXECUTABLE", "C:/tools/dbt.exe")
    assert run_dbt.find_dbt(tmp_path) == "C:/tools/dbt.exe"


def test_main_without_arguments_prints_help(capsys):
    assert run_dbt.main([]) == 2
    assert "Usage" in capsys.readouterr().out


def test_seed_codes_are_unique_and_complete():
    """Same check as the dbt tests, but readable without a database."""
    path = Path(run_dbt.PROJECT_DIR) / "seeds" / "czech_codes.csv"
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    keys = [(row["domain"], row["code"]) for row in rows]
    assert len(keys) == len(set(keys))
    assert all(row["label"] and row["label_fr"] for row in rows)
    loan_codes = {row["code"] for row in rows if row["domain"] == "loan_status"}
    assert loan_codes == {"A", "B", "C", "D"}
