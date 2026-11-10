"""Checks on the real extract in data/raw (row counts and columns).

Skipped when data/raw has not been created yet (run: python -m bank_dw.extract).
Expected values = SELECT COUNT(*) and column lists read on the source database
(relational.fel.cvut.cz, base "financial") on 2026-11-10.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from bank_dw.extract import MANIFEST_NAME, TABLES

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"

EXPECTED = {
    "account": (4_500, ["account_id", "district_id", "frequency", "date"]),
    "card": (892, ["card_id", "disp_id", "type", "issued"]),
    "client": (5_369, ["client_id", "gender", "birth_date", "district_id"]),
    "disp": (5_369, ["disp_id", "client_id", "account_id", "type"]),
    "district": (77, ["district_id"] + [f"A{i}" for i in range(2, 17)]),
    "loan": (682, ["loan_id", "account_id", "date", "amount", "duration", "payments", "status"]),
    "order": (6_471, ["order_id", "account_id", "bank_to", "account_to", "amount", "k_symbol"]),
    "trans": (
        1_056_320,
        [
            "trans_id",
            "account_id",
            "date",
            "type",
            "operation",
            "amount",
            "balance",
            "k_symbol",
            "bank",
            "account",
        ],
    ),
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (RAW_DIR / MANIFEST_NAME).exists(), reason="data/raw not extracted"),
]


@pytest.mark.parametrize("table", TABLES)
def test_raw_file_rows_and_columns(table):
    rows, columns = EXPECTED[table]
    path = RAW_DIR / f"{table}.parquet"
    assert pq.read_metadata(path).num_rows == rows
    assert pq.read_schema(path).names == columns


def test_raw_primary_keys_are_unique():
    for table in TABLES:
        key = EXPECTED[table][1][0]
        values = pq.read_table(RAW_DIR / f"{table}.parquet", columns=[key]).column(key)
        assert len(values.unique()) == len(values), table


def test_manifest_matches_files():
    manifest = json.loads((RAW_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["tables"]) == set(TABLES)
    for table, entry in manifest["tables"].items():
        assert entry["rows"] == EXPECTED[table][0]
