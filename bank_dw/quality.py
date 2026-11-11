"""Automatic data quality report (Q6: "can we trust the data?").

Four families of checks on the raw tables (the data as received from the source):
  1. Orphan keys     - a foreign key that points to nothing (e.g. a transaction on an
                       account that does not exist): joins would silently lose rows.
  2. Duplicates      - a primary key seen twice, or two identical transactions:
                       sums and counts would be too high.
  3. Missing values  - empty cells; some are expected (documented), others are errors.
  4. Consistency     - balances that do not follow the amounts, zero amounts, dates
                       before the opening of the account.

Each check gets a status: ok, info (expected, documented), warning (to know before
using the data) or error (the data must not be used). The report is written to
reports/quality_report.html and reports/quality_report.json. Exit code 1 if any error:
the Airflow task then turns red.

Usage:  python -m bank_dw.quality  [--output-dir reports]
"""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg2
from psycopg2 import sql

from bank_dw.load import connection_settings

DEFAULT_OUTPUT_DIR = Path("reports")
RAW = "raw"
INTERMEDIATE = "intermediate"
STATUSES = ("ok", "info", "warning", "error")

# (child table, foreign key, parent table, parent key) - the links of the Berka schema
RELATIONS = [
    ("account", "district_id", "district", "district_id"),
    ("client", "district_id", "district", "district_id"),
    ("disp", "client_id", "client", "client_id"),
    ("disp", "account_id", "account", "account_id"),
    ("card", "disp_id", "disp", "disp_id"),
    ("loan", "account_id", "account", "account_id"),
    ("order", "account_id", "account", "account_id"),
    ("trans", "account_id", "account", "account_id"),
]

PRIMARY_KEYS = {
    "account": "account_id",
    "card": "card_id",
    "client": "client_id",
    "disp": "disp_id",
    "district": "district_id",
    "loan": "loan_id",
    "order": "order_id",
    "trans": "trans_id",
}

# Columns where empty values are normal, with the reason (PKDD'99 guide or data check).
EXPECTED_MISSING = {
    ("trans", "operation"): "interest lines (k_symbol UROK) have no operation mode",
    ("trans", "k_symbol"): "characterization is optional (guide)",
    ("trans", "bank"): "only transfers have a partner bank",
    ("trans", "account"): "only transfers have a partner account (some other rows use 0)",
    ("order", "k_symbol"): "characterization is optional (guide)",
    ("district", "a12"): "unemployment rate 1995 not given for 1 district",
    ("district", "a15"): "number of crimes 1995 not given for 1 district",
}

# Consistency checks: (name, description, status if problems, SQL returning (checked, problems))
CONSISTENCY_CHECKS = [
    (
        "zero_amounts",
        "Transactions with an amount of 0 CZK (mostly interest or penalty lines).",
        "warning",
        "select count(*), count(*) filter (where amount <= 0) from {raw}.trans",
    ),
    (
        "first_balance_differs_from_amount",
        "Accounts whose first transaction does not give balance = amount "
        "(the account would not start at 0).",
        "warning",
        "with first as ("
        " select distinct on (account_id) account_id, amount, balance from {raw}.trans"
        " order by account_id, date, trans_id)"
        " select count(*), count(*) filter (where amount <> balance) from first",
    ),
    (
        "balance_chain_breaks",
        "Days where previous end-of-day balance + movements of the day differs from the "
        "end-of-day balance by more than 1 CZK per transaction (rounding tolerance).",
        "warning",
        "with days as ("
        " select end_of_day_balance, credits - debits as net, n_transactions,"
        " lag(end_of_day_balance) over (partition by account_id order by balance_date) as prev"
        " from {intermediate}.int_daily_balances)"
        " select count(*), count(*) filter (where abs(prev + net - end_of_day_balance)"
        " > n_transactions) from days where prev is not null",
    ),
    (
        "negative_balances",
        "Transactions leaving a negative balance (overdraft: allowed, but a risk signal).",
        "info",
        "select count(*), count(*) filter (where balance < 0) from {raw}.trans",
    ),
    (
        "transactions_before_account_opening",
        "Transactions dated before the opening of their account.",
        "error",
        "select count(*), count(*) filter (where t.date < a.date)"
        " from {raw}.trans as t join {raw}.account as a using (account_id)",
    ),
    (
        "loans_before_account_opening",
        "Loans granted before the opening of their account.",
        "error",
        "select count(*), count(*) filter (where l.date < a.date)"
        " from {raw}.loan as l join {raw}.account as a using (account_id)",
    ),
]


@dataclass
class CheckResult:
    family: str
    name: str
    description: str
    checked: int | None
    problems: int | None
    status: str
    details: list[dict] = field(default_factory=list)


def status_for(problems: int | None, status_if_problems: str) -> str:
    """ok when there is no problem, otherwise the status chosen for this check."""
    if problems is None:
        return "info"
    return "ok" if problems == 0 else status_if_problems


def overall_status(results: list[CheckResult]) -> str:
    """The worst status of all checks."""
    return max((r.status for r in results), key=STATUSES.index, default="ok")


def _count(cur, query: sql.Composable | str) -> tuple[int, int]:
    cur.execute(query)
    checked, problems = cur.fetchone()
    return int(checked), int(problems or 0)


def check_orphans(cur, raw: str = RAW) -> list[CheckResult]:
    results = []
    for child, fk, parent, pk in RELATIONS:
        query = sql.SQL(
            "select count(*), count(*) filter (where p.{pk} is null)"
            " from {raw}.{child} as c left join {raw}.{parent} as p on p.{pk} = c.{fk}"
            " where c.{fk} is not null"
        ).format(
            raw=sql.Identifier(raw),
            child=sql.Identifier(child),
            parent=sql.Identifier(parent),
            pk=sql.Identifier(pk),
            fk=sql.Identifier(fk),
        )
        checked, problems = _count(cur, query)
        results.append(
            CheckResult(
                "orphan_keys",
                f"{child}.{fk} -> {parent}.{pk}",
                f"Rows of {child} whose {fk} does not exist in {parent}.",
                checked,
                problems,
                status_for(problems, "error"),
            )
        )
    return results


def check_duplicates(cur, raw: str = RAW) -> list[CheckResult]:
    results = []
    for table, pk in PRIMARY_KEYS.items():
        query = sql.SQL("select count(*), count(*) - count(distinct {pk}) from {raw}.{t}").format(
            pk=sql.Identifier(pk), raw=sql.Identifier(raw), t=sql.Identifier(table)
        )
        checked, problems = _count(cur, query)
        results.append(
            CheckResult(
                "duplicates",
                f"{table}.{pk} unique",
                f"Rows of {table} whose primary key {pk} is used more than once.",
                checked,
                problems,
                status_for(problems, "error"),
            )
        )
    # Same account, day, type, operation, amount, balance, symbol and partner: a likely copy.
    cur.execute(
        sql.SQL(
            "select account_id, date, type, operation, k_symbol, amount, balance, count(*) as n"
            " from {raw}.trans group by account_id, date, type, operation, amount, balance,"
            " k_symbol, bank, account having count(*) > 1 order by account_id, date"
        ).format(raw=sql.Identifier(raw))
    )
    columns = [c.name for c in cur.description]
    groups = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    cur.execute(sql.SQL("select count(*) from {raw}.trans").format(raw=sql.Identifier(raw)))
    total = cur.fetchone()[0]
    extra_rows = sum(g["n"] - 1 for g in groups)
    results.append(
        CheckResult(
            "duplicates",
            "identical transactions",
            "Extra copies of transactions identical in every column except trans_id "
            "(same balance after: one of them is probably a copy).",
            total,
            extra_rows,
            status_for(extra_rows, "warning"),
            details=[{k: str(v) for k, v in g.items()} for g in groups[:20]],
        )
    )
    return results


def check_missing_values(cur, raw: str = RAW) -> list[CheckResult]:
    cur.execute(
        "select table_name, column_name, data_type from information_schema.columns"
        " where table_schema = %s and table_name <> '_load_log'"
        " order by table_name, ordinal_position",
        (raw,),
    )
    columns = cur.fetchall()
    results = []
    for table, column, data_type in columns:
        ident = sql.Identifier(column)
        empty = (
            sql.SQL("{c} is null or trim({c}) = ''").format(c=ident)
            if data_type == "text"
            else sql.SQL("{c} is null").format(c=ident)
        )
        query = sql.SQL("select count(*), count(*) filter (where {empty}) from {raw}.{t}").format(
            empty=empty, raw=sql.Identifier(raw), t=sql.Identifier(table)
        )
        checked, problems = _count(cur, query)
        reason = EXPECTED_MISSING.get((table, column))
        description = f"Empty values (NULL or blank text) in {table}.{column}."
        if reason:
            description += f" Expected: {reason}."
        results.append(
            CheckResult(
                "missing_values",
                f"{table}.{column}",
                description,
                checked,
                problems,
                status_for(problems, "info" if reason else "error"),
            )
        )
    return results


def check_consistency(cur, raw: str = RAW, intermediate: str = INTERMEDIATE) -> list[CheckResult]:
    results = []
    for name, description, status_if_problems, query in CONSISTENCY_CHECKS:
        if "{intermediate}" in query and not _table_exists(cur, intermediate, "int_daily_balances"):
            results.append(
                CheckResult(
                    "consistency",
                    name,
                    description + " (skipped: run dbt first)",
                    None,
                    None,
                    "info",
                )
            )
            continue
        formatted = sql.SQL(query).format(
            raw=sql.Identifier(raw), intermediate=sql.Identifier(intermediate)
        )
        checked, problems = _count(cur, formatted)
        results.append(
            CheckResult(
                "consistency",
                name,
                description,
                checked,
                problems,
                status_for(problems, status_if_problems),
            )
        )
    return results


def _table_exists(cur, schema: str, table: str) -> bool:
    cur.execute("select to_regclass(%s)", (f"{schema}.{table}",))
    return cur.fetchone()[0] is not None


def run_checks(conn, raw: str = RAW, intermediate: str = INTERMEDIATE) -> list[CheckResult]:
    with conn.cursor() as cur:
        return [
            *check_orphans(cur, raw),
            *check_duplicates(cur, raw),
            *check_missing_values(cur, raw),
            *check_consistency(cur, raw, intermediate),
        ]


# --- report ---------------------------------------------------------------------------------

FAMILY_TITLES = {
    "orphan_keys": "1. Orphan keys",
    "duplicates": "2. Duplicates",
    "missing_values": "3. Missing values",
    "consistency": "4. Consistency (balances and dates)",
}
COLORS = {"ok": "#1a7f37", "info": "#0969da", "warning": "#9a6700", "error": "#cf222e"}


def _fmt(number: int | None) -> str:
    return "-" if number is None else f"{number:,}".replace(",", " ")


def _badge(status: str) -> str:
    return f'<span class="badge" style="background:{COLORS[status]}">{status}</span>'


def render_html(results: list[CheckResult], generated_at: str, load_info: str) -> str:
    """A self-contained HTML page (no internet needed to open it)."""
    esc = html.escape
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<title>Data quality report - Retail Bank Data Warehouse</title><style>",
        "body{font-family:Segoe UI,Arial,sans-serif;margin:2rem auto;max-width:1100px;"
        "color:#1f2328}",
        "table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}",
        "th,td{border-bottom:1px solid #d0d7de;padding:.35rem .5rem;text-align:left;"
        "font-size:.9rem;vertical-align:top}",
        "td.n{text-align:right;font-variant-numeric:tabular-nums}",
        ".badge{color:#fff;border-radius:1rem;padding:.1rem .6rem;font-size:.8rem}",
        ".cards{display:flex;gap:1rem;flex-wrap:wrap}",
        ".card{border:1px solid #d0d7de;border-radius:.5rem;padding:.75rem 1rem;flex:1;"
        "min-width:200px}",
        "details{margin:.25rem 0}",
        "</style></head><body>",
        "<h1>Data quality report</h1>",
        f"<p>Overall status: {_badge(overall_status(results))} &middot; "
        f"generated {esc(generated_at)}"
        f" &middot; {esc(load_info)}</p>",
        "<p>Source: public Berka database (PKDD'99, Czech bank, 1993-1998), checked in the "
        "<code>raw</code> schema, i.e. as received. Status: <b>ok</b> no problem, "
        "<b>info</b> expected and documented, <b>warning</b> to know before using the data, "
        "<b>error</b> the data must not be used.</p>",
        "<div class='cards'>",
    ]
    for family, title in FAMILY_TITLES.items():
        rows = [r for r in results if r.family == family]
        worst = overall_status(rows)
        n_problems = sum(1 for r in rows if r.status in ("warning", "error"))
        parts.append(
            f"<div class='card'><b>{esc(title)}</b><br>{_badge(worst)} "
            f"{len(rows)} checks, {n_problems} to look at</div>"
        )
    parts.append("</div>")
    for family, title in FAMILY_TITLES.items():
        rows = [r for r in results if r.family == family]
        if family == "missing_values":
            shown = [r for r in rows if r.problems]
            parts.append(
                f"<h2>{esc(title)}</h2><p>{len(rows)} columns checked; "
                f"{len(rows) - len(shown)} have no empty value. Columns with empty values:</p>"
            )
            rows = shown
        else:
            parts.append(f"<h2>{esc(title)}</h2>")
        parts.append(
            "<table><tr><th>Check</th><th>What it looks for</th><th>Rows checked</th>"
            "<th>Problems</th><th>%</th><th>Status</th></tr>"
        )
        for r in rows:
            pct = (
                "-"
                if not r.checked or r.problems is None
                else f"{100 * r.problems / r.checked:.2f}"
            )
            details = ""
            if r.details:
                head = "".join(f"<th>{esc(k)}</th>" for k in r.details[0])
                body = "".join(
                    "<tr>" + "".join(f"<td>{esc(v)}</td>" for v in d.values()) + "</tr>"
                    for d in r.details
                )
                details = (
                    f"<details><summary>{len(r.details)} examples</summary>"
                    f"<table><tr>{head}</tr>{body}</table></details>"
                )
            parts.append(
                f"<tr><td><code>{esc(r.name)}</code></td><td>{esc(r.description)}{details}</td>"
                f"<td class='n'>{_fmt(r.checked)}</td><td class='n'>{_fmt(r.problems)}</td>"
                f"<td class='n'>{pct}</td><td>{_badge(r.status)}</td></tr>"
            )
        parts.append("</table>")
    parts.append("<p><small>Generated by <code>python -m bank_dw.quality</code>.</small></p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def last_load_info(conn, raw: str = RAW) -> str:
    with conn.cursor() as cur:
        if not _table_exists(cur, raw, "_load_log"):
            return "no load recorded"
        cur.execute(
            sql.SQL("select max(loaded_at), max(extracted_at) from {raw}._load_log").format(
                raw=sql.Identifier(raw)
            )
        )
        loaded_at, extracted_at = cur.fetchone()
    return (
        f"data extracted {extracted_at:%Y-%m-%d %H:%M} UTC, loaded {loaded_at:%Y-%m-%d %H:%M} UTC"
    )


def write_report(results: list[CheckResult], output_dir: Path, load_info: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    html_path = output_dir / "quality_report.html"
    html_path.write_text(render_html(results, generated_at, load_info), encoding="utf-8")
    summary = {
        "generated_at": generated_at,
        "load_info": load_info,
        "overall_status": overall_status(results),
        "checks": [asdict(r) for r in results],
    }
    (output_dir / "quality_report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return html_path


def save_results(conn, results: list[CheckResult], schema: str = "quality") -> None:
    """Replace <schema>.check_results by the latest results (read by the Power BI report)."""
    table = sql.SQL("{}.check_results").format(sql.Identifier(schema))
    with conn, conn.cursor() as cur:
        cur.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(schema)))
        cur.execute(
            sql.SQL(
                "create table if not exists {} (family text not null, check_name text not null,"
                " description text, rows_checked bigint, problems bigint, status text not null,"
                " checked_at timestamptz not null default now())"
            ).format(table)
        )
        cur.execute(sql.SQL("truncate table {}").format(table))
        for r in results:
            cur.execute(
                sql.SQL(
                    "insert into {} (family, check_name, description, rows_checked, problems,"
                    " status) values (%s, %s, %s, %s, %s, %s)"
                ).format(table),
                (r.family, r.name, r.description, r.checked, r.problems, r.status),
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the data quality report.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    conn = psycopg2.connect(**connection_settings())
    try:
        results = run_checks(conn)
        load_info = last_load_info(conn)
        save_results(conn, results)
    finally:
        conn.close()
    path = write_report(results, args.output_dir, load_info)
    status = overall_status(results)
    for family, title in FAMILY_TITLES.items():
        rows = [r for r in results if r.family == family]
        counts = {s: sum(r.status == s for r in rows) for s in STATUSES}
        print(f"{title:<38} " + "  ".join(f"{s}={n}" for s, n in counts.items()))
    print(f"Overall status: {status}. Report: {path}")
    return 1 if status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
