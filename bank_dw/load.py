"""Load the Parquet files of data/raw into the PostgreSQL schema "raw".

The table content is copied as is (same rows, same values); only the column names are
put in lower case (A2 -> a2), so that SQL queries do not need quotes.

Each run replaces the raw tables inside ONE transaction: if anything fails, PostgreSQL
cancels everything and the previous tables stay untouched (all or nothing).
Running it twice gives the same tables (idempotent). Each run also adds one line per
table to raw._load_log (history of the loads).

Usage:  python -m bank_dw.load  [--raw-dir data/raw] [--schema raw]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

import psycopg2
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
from dotenv import load_dotenv
from psycopg2 import sql

from bank_dw.extract import DEFAULT_OUTPUT_DIR, MANIFEST_NAME, TABLES

DEFAULT_SCHEMA = "raw"
LOAD_LOG_TABLE = "_load_log"
REQUIRED_ENV = (
    "WAREHOUSE_PG_HOST",
    "WAREHOUSE_PG_PORT",
    "WAREHOUSE_PG_USER",
    "WAREHOUSE_PG_PASSWORD",
    "WAREHOUSE_PG_DB",
)


class LoadError(RuntimeError):
    """Raised when the load cannot be done or its result is wrong."""


def connection_settings(read_env_file: bool = True) -> dict:
    """PostgreSQL settings from the WAREHOUSE_PG_* environment variables (.env file)."""
    if read_env_file:
        load_dotenv()
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise LoadError(
            f"Missing settings: {', '.join(missing)}. Copy .env.example to .env and retry."
        )
    return {
        "host": os.environ["WAREHOUSE_PG_HOST"],
        "port": int(os.environ["WAREHOUSE_PG_PORT"]),
        "user": os.environ["WAREHOUSE_PG_USER"],
        "password": os.environ["WAREHOUSE_PG_PASSWORD"],
        "dbname": os.environ["WAREHOUSE_PG_DB"],
    }


def pg_type(arrow_type: pa.DataType) -> str:
    """Choose the PostgreSQL column type for a Parquet (Arrow) type."""
    if pa.types.is_integer(arrow_type):
        return "bigint"
    if pa.types.is_decimal(arrow_type):
        return f"numeric({arrow_type.precision},{arrow_type.scale})"  # exact amounts
    if pa.types.is_floating(arrow_type):
        return "double precision"
    if pa.types.is_date(arrow_type):
        return "date"
    if pa.types.is_timestamp(arrow_type):
        return "timestamp"
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return "text"
    raise TypeError(f"No PostgreSQL type planned for the Parquet type {arrow_type}")


def create_table_sql(schema: str, table: str, arrow_schema: pa.Schema) -> sql.Composed:
    """CREATE TABLE statement; names are quoted safely (the table "order" is a SQL keyword)."""
    columns = [
        sql.SQL("{} {}{}").format(
            sql.Identifier(field.name.lower()),
            sql.SQL(pg_type(field.type)),
            sql.SQL("" if field.nullable else " not null"),
        )
        for field in arrow_schema
    ]
    return sql.SQL("create table {}.{} ({})").format(
        sql.Identifier(schema), sql.Identifier(table), sql.SQL(", ").join(columns)
    )


def to_csv(data: pa.Table) -> io.BytesIO:
    """CSV in memory for COPY: NULL = empty field, empty text = "" (pyarrow quotes strings)."""
    buffer = io.BytesIO()
    pacsv.write_csv(data, buffer, pacsv.WriteOptions(include_header=False))
    buffer.seek(0)
    return buffer


def read_manifest(raw_dir: Path) -> dict:
    path = raw_dir / MANIFEST_NAME
    if not path.exists():
        raise LoadError(f"{path} not found: run python -m bank_dw.extract first.")
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_schema(cur, schema: str) -> None:
    cur.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(schema)))
    cur.execute(
        sql.SQL(
            "create table if not exists {}.{} ("
            " table_name text not null, rows bigint not null, file_sha256 text not null,"
            " extracted_at timestamptz not null, loaded_at timestamptz not null default now())"
        ).format(sql.Identifier(schema), sql.Identifier(LOAD_LOG_TABLE))
    )


def load_table(cur, schema: str, table: str, path: Path) -> int:
    """Replace <schema>.<table> by the content of the Parquet file; return the row count."""
    data = pq.read_table(path)
    target = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    cur.execute(sql.SQL("drop table if exists {}").format(target))
    cur.execute(create_table_sql(schema, table, data.schema))
    copy = sql.SQL("copy {} from stdin with (format csv)").format(target)
    cur.copy_expert(copy.as_string(cur), to_csv(data))
    cur.execute(sql.SQL("select count(*) from {}").format(target))
    return cur.fetchone()[0]


def load_all(conn, raw_dir: Path = DEFAULT_OUTPUT_DIR, schema: str = DEFAULT_SCHEMA) -> dict:
    """Load every table listed in the manifest, in one transaction. Return {table: rows}."""
    manifest = read_manifest(raw_dir)
    loaded = {}
    with conn:  # one transaction: commit at the end, rollback on any error
        with conn.cursor() as cur:
            ensure_schema(cur, schema)
            for table, entry in manifest["tables"].items():
                rows = load_table(cur, schema, table, raw_dir / entry["file"])
                if rows != entry["rows"]:
                    raise LoadError(f"{table}: {rows} rows loaded, manifest says {entry['rows']}")
                cur.execute(
                    sql.SQL(
                        "insert into {}.{} (table_name, rows, file_sha256, extracted_at)"
                        " values (%s, %s, %s, %s)"
                    ).format(sql.Identifier(schema), sql.Identifier(LOAD_LOG_TABLE)),
                    (table, rows, entry["sha256"], manifest["extracted_at"]),
                )
                loaded[table] = rows
                print(f"{table:<9} {rows:>9,} rows -> {schema}.{table}")
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load data/raw Parquet files into PostgreSQL.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--schema", default=os.environ.get("WAREHOUSE_RAW_SCHEMA", DEFAULT_SCHEMA))
    args = parser.parse_args(argv)
    try:
        conn = psycopg2.connect(**connection_settings())
    except psycopg2.OperationalError as error:
        print(
            f"ERROR: cannot reach PostgreSQL ({error}). Is 'docker compose up -d' running?",
            file=sys.stderr,
        )
        return 1
    except LoadError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    try:
        loaded = load_all(conn, args.raw_dir, args.schema)
    except LoadError as error:
        print(f"ERROR: {error} (nothing was changed)", file=sys.stderr)
        return 1
    finally:
        conn.close()
    missing = sorted(set(TABLES) - set(loaded))
    if missing:
        print(f"Warning: tables not in the manifest: {', '.join(missing)}")
    print(f"OK: {len(loaded)} tables, {sum(loaded.values()):,} rows in schema {args.schema}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
