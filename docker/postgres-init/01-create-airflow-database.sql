-- Runs once, when the PostgreSQL volume is created (empty data folder).
-- The server already has the warehouse database (WAREHOUSE_PG_DB). Airflow keeps its own
-- bookkeeping (DAG runs, task states, logs index) in a separate database.
-- On a volume created before this file existed, run once:
--   docker compose exec postgres sh -c 'createdb -U "$POSTGRES_USER" airflow'
CREATE DATABASE airflow;
