"""Tests of the quality report bank_dw.quality.

Unit tests need nothing. Tests marked "postgres" build small raw tables with known
problems in a throwaway schema of the docker compose database.
"""

from __future__ import annotations

import json
import uuid
from html.parser import HTMLParser

import psycopg2
import pytest

from bank_dw import load, quality
from bank_dw.quality import CheckResult

# --- unit tests ----------------------------------------------------------------------------


def test_status_for():
    assert quality.status_for(0, "error") == "ok"
    assert quality.status_for(3, "error") == "error"
    assert quality.status_for(3, "warning") == "warning"
    assert quality.status_for(None, "error") == "info"  # check skipped


def _result(status: str, family: str = "duplicates", problems: int = 1) -> CheckResult:
    return CheckResult(family, f"check_{status}", "desc", 10, problems, status)


def test_overall_status_is_the_worst():
    assert quality.overall_status([_result("ok"), _result("warning"), _result("info")]) == "warning"
    assert quality.overall_status([_result("ok"), _result("error")]) == "error"
    assert quality.overall_status([]) == "ok"


def test_every_relation_and_table_is_covered():
    tables = {"account", "card", "client", "disp", "district", "loan", "order", "trans"}
    assert set(quality.PRIMARY_KEYS) == tables
    assert {child for child, *_ in quality.RELATIONS} | {"district"} == tables


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.text.append(data)


def test_render_html_escapes_text_and_lists_families():
    results = [
        CheckResult("orphan_keys", "a -> b", "x <script>alert(1)</script>", 5, 0, "ok"),
        CheckResult("missing_values", "t.c", "empty", 5, 2, "info"),
        CheckResult("missing_values", "t.d", "empty", 5, 0, "ok"),
        CheckResult("duplicates", "dup", "copies", 5, 1, "warning", details=[{"account_id": "7"}]),
    ]
    page = quality.render_html(results, "2026-11-11 08:00 UTC", "loaded today")
    assert "<script>" not in page
    parser = _Collector()
    parser.feed(page)
    text = " ".join(parser.text)
    for title in quality.FAMILY_TITLES.values():
        assert title in text
    assert "Overall status" in text and "warning" in text
    assert "t.c" in text and "t.d" not in text  # only columns with empty values are listed
    assert "1 examples" in text


def test_write_report_creates_html_and_json(tmp_path):
    results = [_result("ok", problems=0), _result("warning")]
    path = quality.write_report(results, tmp_path, "loaded today")
    assert path.name == "quality_report.html"
    summary = json.loads((tmp_path / "quality_report.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "warning"
    assert len(summary["checks"]) == 2


# --- tests on the docker compose PostgreSQL --------------------------------------------------

SETUP_SQL = """
create table {s}.district (district_id bigint, a2 text);
create table {s}.client (client_id bigint, district_id bigint);
create table {s}.account (account_id bigint, district_id bigint, date date);
create table {s}.disp (disp_id bigint, client_id bigint, account_id bigint, type text);
create table {s}.card (card_id bigint, disp_id bigint);
create table {s}.loan (loan_id bigint, account_id bigint, date date);
create table {s}."order" (order_id bigint, account_id bigint, k_symbol text);
create table {s}.trans (trans_id bigint, account_id bigint, date date, type text,
    operation text, amount bigint, balance bigint, k_symbol text, bank text, account bigint);
insert into {s}.district values (1, 'Praha'), (2, '  ');            -- blank a2 = missing
insert into {s}.client values (1, 1), (2, 9);                        -- district 9: orphan
insert into {s}.account values (10, 1, '1995-01-01');
insert into {s}.disp values (100, 1, 10, 'OWNER'), (100, 2, 10, 'DISPONENT');  -- dup key
insert into {s}.loan values (1, 10, '1994-06-01');                   -- before opening
insert into {s}.trans values
    (1, 10, '1995-01-01', 'PRIJEM', 'VKLAD', 500, 500, null, null, null),
    (2, 10, '1995-02-01', 'VYDAJ', 'VYBER', 0, 500, null, null, null),
    (3, 10, '1995-02-01', 'VYDAJ', 'VYBER', 0, 500, null, null, null);  -- identical copy
"""


@pytest.fixture
def pg_raw():
    try:
        conn = psycopg2.connect(**load.connection_settings(), connect_timeout=3)
    except (load.LoadError, psycopg2.OperationalError):
        pytest.skip("PostgreSQL not running (docker compose up -d)")
    schema = f"test_quality_{uuid.uuid4().hex[:8]}"
    with conn, conn.cursor() as cur:
        cur.execute(f'create schema "{schema}"')
        cur.execute(SETUP_SQL.format(s=f'"{schema}"'))
    yield conn, schema
    conn.rollback()
    with conn, conn.cursor() as cur:
        cur.execute(f'drop schema "{schema}" cascade')
    conn.close()


def _by_name(results):
    return {r.name: r for r in results}


@pytest.mark.postgres
def test_checks_find_the_planted_problems(pg_raw):
    conn, schema = pg_raw
    results = _by_name(quality.run_checks(conn, raw=schema, intermediate="no_such_schema"))
    assert results["client.district_id -> district.district_id"].status == "error"
    assert results["client.district_id -> district.district_id"].problems == 1
    assert results["account.district_id -> district.district_id"].status == "ok"
    assert results["disp.disp_id unique"].problems == 1
    assert results["identical transactions"].problems == 1
    assert results["identical transactions"].status == "warning"
    assert results["district.a2"].status == "error"  # blank text, not expected
    assert results["trans.k_symbol"].status == "info"  # expected empty values
    assert results["zero_amounts"].problems == 2
    assert results["loans_before_account_opening"].status == "error"
    assert results["balance_chain_breaks"].checked is None  # skipped: no dbt tables
    assert quality.overall_status(list(results.values())) == "error"


@pytest.mark.postgres
def test_save_results_replaces_the_table(pg_raw):
    conn, schema = pg_raw
    results = [_result("ok", problems=0), _result("warning")]
    quality.save_results(conn, results, schema=schema)
    quality.save_results(conn, results, schema=schema)  # replaced, not appended
    with conn.cursor() as cur:
        cur.execute(f'select status, count(*) from "{schema}".check_results group by 1 order by 1')
        assert cur.fetchall() == [("ok", 1), ("warning", 1)]
