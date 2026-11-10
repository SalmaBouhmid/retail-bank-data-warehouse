"""Extract the 8 tables of the public Berka database (PKDD'99) into Parquet files.

Source: https://relational.fel.cvut.cz/dataset/Financial (MariaDB, public guest account).

Each run rewrites data/raw/<table>.parquet and data/raw/_manifest.json.
Running it twice gives the same Parquet files (idempotent): no duplicates, no leftovers.
The manifest records when the extraction ran and what it produced.

Usage:  python -m bank_dw.extract  [--tables trans loan] [--output-dir data/raw]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
from sqlalchemy import Column, Connection, MetaData, Table, create_engine, func, select
from sqlalchemy import types as sqltypes
from sqlalchemy.engine import URL, Engine

TABLES = ("account", "card", "client", "disp", "district", "loan", "order", "trans")
DEFAULT_OUTPUT_DIR = Path("data/raw")
MANIFEST_NAME = "_manifest.json"
CHUNK_SIZE = 100_000  # rows per query: trans (~1 M rows) is read in about 11 queries
SOURCE_URL = "https://relational.fel.cvut.cz/dataset/Financial"
REQUIRED_ENV = ("BERKA_HOST", "BERKA_PORT", "BERKA_USER", "BERKA_PASSWORD", "BERKA_DATABASE")


class ExtractError(RuntimeError):
    """Raised when the extraction cannot be done or its result is wrong."""


def build_url(read_env_file: bool = True) -> URL:
    """Build the MariaDB connection URL from the BERKA_* environment variables (.env file)."""
    if read_env_file:
        load_dotenv()
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise ExtractError(
            f"Missing settings: {', '.join(missing)}. Copy .env.example to .env and retry."
        )
    return URL.create(
        "mysql+pymysql",
        username=os.environ["BERKA_USER"],
        password=os.environ["BERKA_PASSWORD"],
        host=os.environ["BERKA_HOST"],
        port=int(os.environ["BERKA_PORT"]),
        database=os.environ["BERKA_DATABASE"],
    )


def arrow_type(sql_type: sqltypes.TypeEngine) -> pa.DataType:
    """Choose the Parquet (Arrow) type for a database column type."""
    if isinstance(sql_type, sqltypes.Integer):
        return pa.int64()
    if isinstance(sql_type, sqltypes.Float):  # Float is a kind of Numeric: test it first
        return pa.float64()
    if isinstance(sql_type, sqltypes.Numeric):
        if sql_type.precision is None:
            return pa.float64()
        # keep exact amounts (e.g. DECIMAL(6,2)) instead of rounding them to floats
        return pa.decimal128(sql_type.precision, sql_type.scale or 0)
    if isinstance(sql_type, sqltypes.DateTime):
        return pa.timestamp("s")
    if isinstance(sql_type, sqltypes.Date):
        return pa.date32()
    if isinstance(sql_type, sqltypes.String):
        return pa.string()
    raise TypeError(f"No Parquet type planned for the column type {sql_type!r}")


def arrow_schema(table: Table) -> pa.Schema:
    """Same column names and order as the source table."""
    return pa.schema(
        [pa.field(col.name, arrow_type(col.type), nullable=col.nullable) for col in table.columns]
    )


def single_primary_key(table: Table) -> Column:
    """Return the one primary key column, used to read the table chunk by chunk."""
    keys = list(table.primary_key.columns)
    if len(keys) != 1:
        raise ExtractError(f"Table {table.name}: expected 1 primary key column, found {len(keys)}")
    return keys[0]


def read_table(conn: Connection, name: str, chunk_size: int = CHUNK_SIZE) -> pa.Table:
    """Read a whole source table, chunk by chunk, sorted by its primary key.

    Each query asks for the next rows after the last key already read
    ("keyset pagination"): the order is always the same, so the file is too.
    """
    table = Table(name, MetaData(), autoload_with=conn)
    key = single_primary_key(table)
    schema = arrow_schema(table)
    batches: list[pa.RecordBatch] = []
    last_key = None
    while True:
        query = select(table).order_by(key).limit(chunk_size)
        if last_key is not None:
            query = query.where(key > last_key)
        rows = conn.execute(query).all()
        if not rows:
            break
        columns = list(zip(*rows, strict=True))
        arrays = [
            pa.array(values, type=field.type) for values, field in zip(columns, schema, strict=True)
        ]
        batches.append(pa.record_batch(arrays, schema=schema))
        last_key = rows[-1]._mapping[key.name]
        if len(rows) < chunk_size:
            break
    return pa.Table.from_batches(batches, schema=schema)


def count_rows(conn: Connection, name: str) -> int:
    """Number of rows in the source table (to check the file afterwards)."""
    table = Table(name, MetaData(), autoload_with=conn)
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def write_parquet(data: pa.Table, path: Path) -> None:
    """Write to a temporary file, then rename: a crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(data, tmp, compression="zstd")
    os.replace(tmp, path)


def verify_parquet(path: Path, expected_rows: int, expected_columns: list[str]) -> None:
    """Check that the file has the source row count and the source columns."""
    metadata = pq.read_metadata(path)
    columns = pq.read_schema(path).names
    if metadata.num_rows != expected_rows:
        raise ExtractError(f"{path.name}: {metadata.num_rows} rows, source has {expected_rows}")
    if columns != expected_columns:
        raise ExtractError(f"{path.name}: columns {columns}, source has {expected_columns}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_table(conn: Connection, name: str, output_dir: Path, chunk_size: int) -> dict:
    """Extract one table to <output_dir>/<name>.parquet and return its manifest entry."""
    data = read_table(conn, name, chunk_size)
    path = output_dir / f"{name}.parquet"
    write_parquet(data, path)
    source_rows = count_rows(conn, name)
    verify_parquet(path, source_rows, data.schema.names)
    return {
        "file": path.name,
        "rows": source_rows,
        "columns": data.schema.names,
        "sha256": sha256(path),
    }


def extract_all(
    engine: Engine,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    tables: Iterable[str] = TABLES,
    chunk_size: int = CHUNK_SIZE,
) -> dict:
    """Extract the tables, then write the manifest (written last: it means "all files are OK")."""
    output_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    entries = {}
    with engine.connect() as conn:
        for name in tables:
            entries[name] = extract_table(conn, name, output_dir, chunk_size)
            print(f"{name:<9} {entries[name]['rows']:>9,} rows -> {entries[name]['file']}")
    manifest = {
        "source": SOURCE_URL,
        "database": engine.url.database,
        "extracted_at": started.isoformat(timespec="seconds"),
        "tables": entries,
    }
    manifest_path = output_dir / MANIFEST_NAME
    tmp = manifest_path.with_name(MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract the Berka tables to Parquet files.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--tables", nargs="+", choices=TABLES, default=list(TABLES))
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    args = parser.parse_args(argv)
    try:
        engine = create_engine(build_url())
        manifest = extract_all(engine, args.output_dir, args.tables, args.chunk_size)
    except ExtractError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    total = sum(entry["rows"] for entry in manifest["tables"].values())
    print(f"OK: {len(manifest['tables'])} tables, {total:,} rows, manifest in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
