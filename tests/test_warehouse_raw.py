"""Checks on the real schema "raw" in the docker compose PostgreSQL (after python -m bank_dw.load).

Skipped when PostgreSQL is not running or the raw tables are not loaded yet.
"""

from __future__ import annotations

import psycopg2
import pytest

from bank_dw import load
from tests.test_raw_files import EXPECTED


@pytest.fixture(scope="module")
def conn():
    try:
        connection = psycopg2.connect(**load.connection_settings(), connect_timeout=3)
    except (load.LoadError, psycopg2.OperationalError):
        pytest.skip("PostgreSQL not running (docker compose up -d)")
    with connection.cursor() as cur:
        cur.execute("select to_regclass('raw.trans')")
        if cur.fetchone()[0] is None:
            pytest.skip("schema raw not loaded (python -m bank_dw.load)")
    yield connection
    connection.close()


@pytest.mark.postgres
@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_raw_table_rows_and_columns(conn, table):
    rows, columns = EXPECTED[table]
    with conn.cursor() as cur:
        cur.execute(f'select count(*) from raw."{table}"')
        assert cur.fetchone()[0] == rows
        cur.execute(
            "select column_name from information_schema.columns"
            " where table_schema = 'raw' and table_name = %s order by ordinal_position",
            (table,),
        )
        assert [r[0] for r in cur.fetchall()] == [c.lower() for c in columns]
