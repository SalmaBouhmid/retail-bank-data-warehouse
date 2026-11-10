"""Airflow DAG: the whole warehouse, every day.

    extract  ->  load  ->  dbt_run  ->  dbt_test
    (MariaDB -> Parquet)  (Parquet -> schema raw)  (seed + models)  (data tests)

- Runs every day at 05:00 UTC. The source does not change any more (data 1993-1998), but a
  daily run shows what a real pipeline does: history of runs, logs, retries, failures.
- extract re-downloads the source only if the copy in data/raw is older than 7 days
  (about 8 minutes of download: we stay polite with the public server). The parameter
  force_extract=true forces the download.
- If a task fails, Airflow tries it again 2 times, 2 minutes apart.
- The parameter simulate_failure=true makes "load" fail on its first try only: it shows a
  retry in the logs without breaking anything.
- dbt_test fails (in red) if one dbt test fails, e.g. a code without translation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Param, dag, task

PROJECT_DIR = "/opt/project"
MAX_EXTRACT_AGE_DAYS = 7


@dag(
    dag_id="bank_dw_daily",
    schedule="0 5 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,  # do not replay the past days: each run rebuilds everything
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=2)},
    params={
        "force_extract": Param(False, type="boolean", description="Download the source again"),
        "simulate_failure": Param(
            False, type="boolean", description="Make 'load' fail on its first try (retry demo)"
        ),
    },
    tags=["retail-bank"],
    doc_md=__doc__,
)
def bank_dw_daily():
    @task
    def extract(**context) -> str:
        """Copy the 8 source tables to data/raw (Parquet), unless the copy is recent."""
        from pathlib import Path

        from sqlalchemy import create_engine

        from bank_dw import extract as ex

        raw_dir = Path(PROJECT_DIR) / ex.DEFAULT_OUTPUT_DIR
        age = ex.extract_age_days(raw_dir)
        if (
            age is not None
            and age < MAX_EXTRACT_AGE_DAYS
            and not context["params"]["force_extract"]
        ):
            print(f"Copy in {raw_dir} is {age:.1f} days old (< {MAX_EXTRACT_AGE_DAYS}): kept.")
            return "kept"
        manifest = ex.extract_all(create_engine(ex.build_url()), raw_dir)
        return f"extracted at {manifest['extracted_at']}"

    @task
    def load(extract_result: str, **context) -> int:
        """Replace the raw tables in PostgreSQL by the Parquet files (one transaction)."""
        from pathlib import Path

        import psycopg2

        from bank_dw import load as ld
        from bank_dw.extract import DEFAULT_OUTPUT_DIR

        try_number = context["ti"].try_number
        print(f"extract said: {extract_result}. Try number {try_number}.")
        if context["params"]["simulate_failure"] and try_number == 1:
            raise RuntimeError("Simulated failure (simulate_failure=true): Airflow will retry.")
        conn = psycopg2.connect(**ld.connection_settings())
        try:
            loaded = ld.load_all(conn, Path(PROJECT_DIR) / DEFAULT_OUTPUT_DIR)
        finally:
            conn.close()
        return sum(loaded.values())

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=f"cd {PROJECT_DIR} && python -m bank_dw.run_dbt seed "
        "&& python -m bank_dw.run_dbt run",
    )
    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"cd {PROJECT_DIR} && python -m bank_dw.run_dbt test",
    )

    load(extract()) >> dbt_run >> dbt_test


bank_dw_daily()
