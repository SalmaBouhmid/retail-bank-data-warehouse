"""Tests of bank_dw.extract on a small local SQLite database (no network needed)."""

from __future__ import annotations

import datetime as dt
import json
import warnings
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
)
from sqlalchemy.engine import Engine

from bank_dw import extract

# SQLite stores DECIMAL as float and SQLAlchemy warns about it: not a problem for these tests.
warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")

ACCOUNT_ROWS = [
    {
        "account_id": i,
        "district_id": 10 + i,
        "frequency": "POPLATEK MESICNE",
        "date": dt.date(1993, 1, i),
    }
    for i in range(1, 8)  # 7 rows: more than one chunk when chunk_size=3
]
ORDER_ROWS = [
    {"order_id": 1, "account_id": 1, "amount": Decimal("2452.0"), "k_symbol": "SIPO"},
    {"order_id": 2, "account_id": 2, "amount": Decimal("3372.7"), "k_symbol": None},
]


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """A tiny copy of two Berka tables, including the table named "order" (SQL keyword)."""
    eng = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    meta = MetaData()
    account = Table(
        "account",
        meta,
        Column("account_id", Integer, primary_key=True),
        Column("district_id", Integer, nullable=False),
        Column("frequency", String(18), nullable=False),
        Column("date", Date, nullable=False),
    )
    order = Table(
        "order",
        meta,
        Column("order_id", Integer, primary_key=True),
        Column("account_id", Integer, nullable=False),
        Column("amount", Numeric(6, 1), nullable=False),
        Column("k_symbol", String(8), nullable=True),
    )
    meta.create_all(eng)
    with eng.begin() as conn:
        conn.execute(account.insert(), ACCOUNT_ROWS)
        conn.execute(order.insert(), ORDER_ROWS)
    return eng


# --- column types -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        (Integer(), pa.int64()),
        (Numeric(6, 2), pa.decimal128(6, 2)),
        (Numeric(), pa.float64()),
        (Float(), pa.float64()),
        (Date(), pa.date32()),
        (DateTime(), pa.timestamp("s")),
        (String(10), pa.string()),
    ],
)
def test_arrow_type(sql_type, expected):
    assert extract.arrow_type(sql_type) == expected


def test_arrow_type_unknown_raises():
    with pytest.raises(TypeError):
        extract.arrow_type(LargeBinary())


# --- reading ------------------------------------------------------------------------------


def test_read_table_reads_all_rows_across_chunks(engine):
    with engine.connect() as conn:
        data = extract.read_table(conn, "account", chunk_size=3)
    assert data.num_rows == len(ACCOUNT_ROWS)
    assert data.column("account_id").to_pylist() == list(range(1, 8))


def test_read_table_chunk_size_equal_to_row_count(engine):
    with engine.connect() as conn:
        data = extract.read_table(conn, "account", chunk_size=7)
    assert data.num_rows == 7


def test_read_table_keeps_column_names_and_order(engine):
    with engine.connect() as conn:
        data = extract.read_table(conn, "account")
    assert data.schema.names == ["account_id", "district_id", "frequency", "date"]


def test_read_table_keeps_values_and_types(engine):
    with engine.connect() as conn:
        data = extract.read_table(conn, "account")
    first = data.to_pylist()[0]
    assert first == ACCOUNT_ROWS[0]
    assert data.schema.field("date").type == pa.date32()


def test_read_table_handles_sql_keyword_table_and_nulls(engine):
    with engine.connect() as conn:
        data = extract.read_table(conn, "order")
    assert data.column("k_symbol").to_pylist() == ["SIPO", None]
    assert data.column("amount").to_pylist() == [Decimal("2452.0"), Decimal("3372.7")]


def test_read_table_without_single_primary_key_raises(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE no_key (a INTEGER, b INTEGER)")
        with pytest.raises(extract.ExtractError):
            extract.read_table(conn, "no_key")


# --- checks on the written file ------------------------------------------------------------


def _write_sample(path: Path) -> None:
    pq.write_table(pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]}), path)


def test_verify_parquet_accepts_matching_file(tmp_path):
    path = tmp_path / "t.parquet"
    _write_sample(path)
    extract.verify_parquet(path, 3, ["a", "b"])  # no error


def test_verify_parquet_wrong_row_count_raises(tmp_path):
    path = tmp_path / "t.parquet"
    _write_sample(path)
    with pytest.raises(extract.ExtractError, match="rows"):
        extract.verify_parquet(path, 4, ["a", "b"])


def test_verify_parquet_wrong_columns_raises(tmp_path):
    path = tmp_path / "t.parquet"
    _write_sample(path)
    with pytest.raises(extract.ExtractError, match="columns"):
        extract.verify_parquet(path, 3, ["a", "c"])


# --- full extraction ---------------------------------------------------------------------


def test_extract_all_writes_files_and_manifest(engine, tmp_path):
    out = tmp_path / "raw"
    manifest = extract.extract_all(engine, out, ["account", "order"], chunk_size=3)
    assert (out / "account.parquet").exists()
    assert (out / "order.parquet").exists()
    saved = json.loads((out / extract.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert saved == manifest
    assert saved["tables"]["account"]["rows"] == 7
    assert saved["tables"]["order"]["columns"] == ["order_id", "account_id", "amount", "k_symbol"]
    assert dt.datetime.fromisoformat(saved["extracted_at"]).tzinfo is not None


def test_extract_all_is_idempotent(engine, tmp_path):
    out = tmp_path / "raw"
    first = extract.extract_all(engine, out, ["account", "order"], chunk_size=3)
    second = extract.extract_all(engine, out, ["account", "order"], chunk_size=3)
    assert first["tables"] == second["tables"]  # same rows, columns and file hashes
    assert sorted(p.name for p in out.iterdir()) == [
        "_manifest.json",
        "account.parquet",
        "order.parquet",
    ]  # no duplicate and no leftover .tmp file


def test_extract_all_replaces_old_content(engine, tmp_path):
    out = tmp_path / "raw"
    out.mkdir()
    _write_sample(out / "account.parquet")  # stale file from an older run
    extract.extract_all(engine, out, ["account"])
    assert pq.read_metadata(out / "account.parquet").num_rows == 7


# --- settings and command line ------------------------------------------------------------


def test_build_url_reads_environment(monkeypatch):
    for name, value in {
        "BERKA_HOST": "db.example",
        "BERKA_PORT": "3306",
        "BERKA_USER": "u",
        "BERKA_PASSWORD": "p",
        "BERKA_DATABASE": "financial",
    }.items():
        monkeypatch.setenv(name, value)
    url = extract.build_url(read_env_file=False)
    assert (url.host, url.port, url.database) == ("db.example", 3306, "financial")


def test_build_url_missing_settings_raises(monkeypatch):
    for name in extract.REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(extract.ExtractError, match=".env.example"):
        extract.build_url(read_env_file=False)


def test_tables_list_has_the_8_berka_tables():
    assert sorted(extract.TABLES) == sorted(
        ["account", "card", "client", "disp", "district", "loan", "order", "trans"]
    )


def test_main_rejects_unknown_table():
    with pytest.raises(SystemExit):
        extract.main(["--tables", "customers"])


# --- age of the last extraction (used by the Airflow DAG) -----------------------------------


def test_extract_age_days_without_manifest_is_none(tmp_path):
    assert extract.extract_age_days(tmp_path) is None


def test_extract_age_days_reads_the_manifest(tmp_path):
    manifest = {"extracted_at": "2026-11-01T00:00:00+00:00", "tables": {}}
    (tmp_path / extract.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    now = dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.UTC)
    assert extract.extract_age_days(tmp_path, now) == pytest.approx(3.5)
