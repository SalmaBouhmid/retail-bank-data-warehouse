"""Tests of the Airflow DAG. Airflow is only installed in the Docker image: run them with
    docker compose exec airflow python -m pytest /opt/project/tests/test_dag.py
They are skipped on the laptop (no Airflow there)."""

from __future__ import annotations

from pathlib import Path

import pytest

# "airflow.models", not "airflow": the local folder airflow/ (DAGs) would be found instead
pytest.importorskip("airflow.models")

from airflow.models import DagBag  # noqa: E402

DAGS_DIR = Path(__file__).resolve().parents[1] / "airflow" / "dags"


@pytest.fixture(scope="module")
def dag():
    bag = DagBag(dag_folder=str(DAGS_DIR))  # examples are off (AIRFLOW__CORE__LOAD_EXAMPLES)
    assert bag.import_errors == {}
    return bag.get_dag("bank_dw_daily")


def test_dag_has_the_5_tasks_in_order(dag):
    assert sorted(dag.task_ids) == ["dbt_run", "dbt_test", "extract", "load", "quality_report"]
    assert dag.get_task("extract").downstream_task_ids == {"load"}
    assert dag.get_task("load").downstream_task_ids == {"dbt_run"}
    assert dag.get_task("dbt_run").downstream_task_ids == {"dbt_test"}
    assert dag.get_task("dbt_test").downstream_task_ids == {"quality_report"}


def test_quality_report_runs_even_if_dbt_test_failed(dag):
    assert dag.get_task("quality_report").trigger_rule == "all_done"


def test_dag_retries_and_schedule(dag):
    assert all(task.retries == 2 for task in dag.tasks)
    assert dag.catchup is False
    assert dag.max_active_runs == 1


def test_dag_parameters_default_to_false(dag):
    assert dag.params["force_extract"] is False
    assert dag.params["simulate_failure"] is False
