"""Tests of bank_dw.load.

Unit tests need nothing. Tests marked "postgres" use the docker compose database
(settings from .env) inside a throwaway schema, and are skipped if it is not running.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg2
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bank_dw import load
from bank_dw.extract import MANIFEST_NAME

# --- unit tests ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arrow_type", "expected"),
    [
        (pa.int64(), "bigint"),
        (pa.decimal128(6, 2), "numeric(6,2)"),
        (pa.float64(), "double precision"),
        (pa.date32(), "date"),
        (pa.timestamp("s"), "timestamp"),
        (pa.string(), "text"),
    ],
)
def test_pg_type(arrow_type, expected):
    assert load.pg_type(arrow_type) == expected


def test_pg_type_unknown_raises():
    with pytest.raises(TypeError):
        load.pg_type(pa.binary())


def test_to_csv_keeps_null_and_empty_text_apart():
    data = pa.table({"s": ["", None, "a,b"], "n": [1, None, 3]})
    assert load.to_csv(data).read() == b'"",1\n,\n"a,b",3\n'


def test_connection_settings_reads_environment(monkeypatch):
    for name, value in zip(load.REQUIRED_ENV, ["h", "5434", "u", "p", "d"], strict=True):
        monkeypatch.setenv(name, value)
    settings = load.connection_settings(read_env_file=False)
    assert settings == {"host": "h", "port": 5434, "user": "u", "password": "p", "dbname": "d"}


def test_connection_settings_missing_raises(monkeypatch):
    for name in load.REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(load.LoadError, match=".env.example"):
        load.connection_settings(read_env_file=False)


def test_read_manifest_missing_raises(tmp_path):
    with pytest.raises(load.LoadError, match="extract"):
        load.read_manifest(tmp_path)


# --- tests on the docker compose PostgreSQL --------------------------------------------------


def _connect():
    try:
        return psycopg2.connect(**load.connection_settings(), connect_timeout=3)
    except (load.LoadError, psycopg2.OperationalError):
        return None


@pytest.fixture
def pg():
    """Connection + a throwaway schema name, dropped after the test."""
    conn = _connect()
    if conn is None:
        pytest.skip("PostgreSQL not running (docker compose up -d)")
    schema = f"test_raw_{uuid.uuid4().hex[:8]}"
    yield conn, schema
    conn.rollback()
    with conn, conn.cursor() as cur:
        cur.execute(f'drop schema if exists "{schema}" cascade')
    conn.close()


ORDER = pa.table(
    {
        "order_id": pa.array([1, 2, 3], pa.int64()),
        "amount": pa.array(
            [Decimal("2452.0"), Decimal("3372.7"), Decimal("0.5")], pa.decimal128(6, 1)
        ),
        "k_symbol": pa.array(["SIPO", None, ""], pa.string()),
        "date": pa.array([dt.date(1993, 1, 1)] * 3, pa.date32()),
    }
)
DISTRICT = pa.table({"district_id": pa.array([1], pa.int64()), "A2": pa.array(["Praha"])})


def _write_raw(raw_dir: Path, rows_in_manifest: int | None = None) -> None:
    """Write two Parquet files and their manifest, like bank_dw.extract does."""
    raw_dir.mkdir(exist_ok=True)
    tables = {"order": ORDER, "district": DISTRICT}
    entries = {}
    for name, data in tables.items():
        pq.write_table(data, raw_dir / f"{name}.parquet")
        entries[name] = {"file": f"{name}.parquet", "rows": data.num_rows, "sha256": "x"}
    if rows_in_manifest is not None:
        entries["order"]["rows"] = rows_in_manifest
    manifest = {"extracted_at": "2026-11-10T16:00:00+00:00", "tables": entries}
    (raw_dir / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")


def _query(conn, query: str):
    with conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()


@pytest.mark.postgres
def test_load_all_copies_rows_and_values(pg, tmp_path):
    conn, schema = pg
    _write_raw(tmp_path)
    assert load.load_all(conn, tmp_path, schema) == {"order": 3, "district": 1}
    rows = _query(
        conn, f'select order_id, amount, k_symbol, date from "{schema}"."order" order by 1'
    )
    assert rows == [
        (1, Decimal("2452.0"), "SIPO", dt.date(1993, 1, 1)),
        (2, Decimal("3372.7"), None, dt.date(1993, 1, 1)),  # NULL stays NULL
        (3, Decimal("0.5"), "", dt.date(1993, 1, 1)),  # empty text stays empty text
    ]


@pytest.mark.postgres
def test_load_all_lowercases_column_names(pg, tmp_path):
    conn, schema = pg
    _write_raw(tmp_path)
    load.load_all(conn, tmp_path, schema)
    assert _query(conn, f'select a2 from "{schema}".district') == [("Praha",)]


@pytest.mark.postgres
def test_load_all_is_idempotent_and_logs_each_run(pg, tmp_path):
    conn, schema = pg
    _write_raw(tmp_path)
    load.load_all(conn, tmp_path, schema)
    load.load_all(conn, tmp_path, schema)
    assert _query(conn, f'select count(*) from "{schema}"."order"') == [(3,)]  # not 6
    log = _query(
        conn, f'select table_name, count(*) from "{schema}"._load_log group by 1 order by 1'
    )
    assert log == [("district", 2), ("order", 2)]


@pytest.mark.postgres
def test_load_all_rolls_back_everything_on_error(pg, tmp_path):
    conn, schema = pg
    _write_raw(tmp_path)
    load.load_all(conn, tmp_path, schema)
    _write_raw(tmp_path, rows_in_manifest=999)  # the check will fail on "order"
    with pytest.raises(load.LoadError, match="999"):
        load.load_all(conn, tmp_path, schema)
    # the tables of the first load are still there, untouched
    assert _query(conn, f'select count(*) from "{schema}"."order"') == [(3,)]
    assert _query(conn, f'select count(*) from "{schema}"._load_log') == [(2,)]
