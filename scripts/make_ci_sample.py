"""Build the small sample of the Berka data used by the CI (tests/fixtures/berka_sample).

GitHub Actions cannot download the full database at each push (about 8 minutes from the
server in Prague), so the CI loads a sample instead: about 120 accounts with ALL their rows
(clients, dispositions, cards, loans, orders, transactions) and the 77 districts.
The sample keeps every link valid, so all dbt tests are meaningful.

Selection (deterministic): accounts whose id is a multiple of 40, plus the first 3 loans of
each status (A, B, C, D) so that every loan status is present.

Usage (after python -m bank_dw.extract):  python scripts/make_ci_sample.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bank_dw.extract import MANIFEST_NAME, SOURCE_URL, sha256, write_parquet  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "tests" / "fixtures" / "berka_sample"


def read(name: str) -> pa.Table:
    return pq.read_table(RAW / f"{name}.parquet")


def keep(table: pa.Table, column: str, values: set[int]) -> pa.Table:
    return table.filter(pc.is_in(table[column], value_set=pa.array(sorted(values), pa.int64())))


def main() -> int:
    account, loan = read("account"), read("loan")
    accounts = {a for a in account["account_id"].to_pylist() if a % 40 == 0}
    loans = loan.to_pylist()
    for status in "ABCD":
        with_status = sorted(row["account_id"] for row in loans if row["status"] == status)
        accounts |= set(with_status[:3])
    disp = keep(read("disp"), "account_id", accounts)
    tables = {
        "account": keep(account, "account_id", accounts),
        "disp": disp,
        "client": keep(read("client"), "client_id", set(disp["client_id"].to_pylist())),
        "card": keep(read("card"), "disp_id", set(disp["disp_id"].to_pylist())),
        "loan": keep(loan, "account_id", accounts),
        "order": keep(read("order"), "account_id", accounts),
        "trans": keep(read("trans"), "account_id", accounts),
        "district": read("district"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    entries = {}
    for name, data in tables.items():
        path = OUT / f"{name}.parquet"
        write_parquet(data, path)
        entries[name] = {
            "file": path.name,
            "rows": data.num_rows,
            "columns": data.schema.names,
            "sha256": sha256(path),
        }
        print(f"{name:<9} {data.num_rows:>7,} rows")
    manifest = {
        "source": SOURCE_URL,
        "database": "financial (CI sample, see scripts/make_ci_sample.py)",
        "extracted_at": json.loads((RAW / MANIFEST_NAME).read_text(encoding="utf-8"))[
            "extracted_at"
        ],
        "tables": entries,
    }
    (OUT / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
