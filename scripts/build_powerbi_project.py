"""Generate the Power BI project (PBIP) of the warehouse: semantic model + 3 report pages.

A PBIP project is a folder of text files (TMDL for the model, JSON for the report) that
Power BI Desktop opens like a .pbix, and that Git can version line by line.

    powerbi/BankDW.pbip                    <- open this file in Power BI Desktop
    powerbi/BankDW.SemanticModel/          <- tables, relationships, DAX measures (TMDL)
    powerbi/BankDW.Report/                 <- 3 pages and their visuals (PBIR JSON)

The tables and columns are read from the PostgreSQL warehouse (schemas marts and quality),
so the model always matches the dbt models. Run once, after "dbt build" and the quality
report; afterwards the project is edited and saved in Power BI Desktop (never overwritten):

    python scripts/build_powerbi_project.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bank_dw.load import connection_settings  # noqa: E402

OUT = ROOT / "powerbi"
NAME = "BankDW"
SCHEMA_URL = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"

# Tables of the model: (table in Power BI, PostgreSQL schema, PostgreSQL table)
TABLES = [
    ("dim_date", "marts", "dim_date"),
    ("dim_client", "marts", "dim_client"),
    ("dim_account", "marts", "dim_account"),
    ("dim_district", "marts", "dim_district"),
    ("fct_transactions", "marts", "fct_transactions"),
    ("fct_monthly_balance", "marts", "fct_monthly_balance"),
    ("fct_loans", "marts", "fct_loans"),
    ("fct_cards", "marts", "fct_cards"),
    ("check_results", "quality", "check_results"),
]

# Star schema: (fact column, dimension column). One active relationship each.
RELATIONSHIPS = [
    ("fct_transactions.account_id", "dim_account.account_id"),
    ("fct_transactions.owner_client_id", "dim_client.client_id"),
    ("fct_transactions.district_id", "dim_district.district_id"),
    ("fct_transactions.transaction_date", "dim_date.date_day"),
    ("fct_monthly_balance.account_id", "dim_account.account_id"),
    ("fct_monthly_balance.owner_client_id", "dim_client.client_id"),
    ("fct_monthly_balance.district_id", "dim_district.district_id"),
    ("fct_monthly_balance.month_start", "dim_date.date_day"),
    ("fct_loans.account_id", "dim_account.account_id"),
    ("fct_loans.owner_client_id", "dim_client.client_id"),
    ("fct_loans.district_id", "dim_district.district_id"),
    ("fct_loans.granted_on", "dim_date.date_day"),
    ("fct_cards.client_id", "dim_client.client_id"),
    ("fct_cards.account_id", "dim_account.account_id"),
    ("fct_cards.issued_on", "dim_date.date_day"),
]

# DAX measures: (table, name, expression, format). Names in French: they are report labels.
MEASURES = [
    ("dim_client", "Clients", "COUNTROWS(dim_client)", "#,0"),
    ("dim_account", "Comptes", "COUNTROWS(dim_account)", "#,0"),
    ("fct_transactions", "Transactions", "COUNTROWS(fct_transactions)", "#,0"),
    (
        "fct_transactions",
        "Volume crédits (CZK)",
        'CALCULATE(SUM(fct_transactions[amount]), fct_transactions[direction] = "credit")',
        "#,0",
    ),
    (
        "fct_transactions",
        "Volume débits (CZK)",
        'CALCULATE(SUM(fct_transactions[amount]), fct_transactions[direction] = "debit")',
        "#,0",
    ),
    (
        "fct_monthly_balance",
        "Solde moyen fin de mois (CZK)",
        "AVERAGE(fct_monthly_balance[month_end_balance])",
        "#,0",
    ),
    ("fct_loans", "Prêts", "COUNTROWS(fct_loans)", "#,0"),
    (
        "fct_loans",
        "Prêts en défaut",
        "CALCULATE(COUNTROWS(fct_loans), fct_loans[is_default] = TRUE())",
        "#,0",
    ),
    ("fct_loans", "Taux de défaut", "DIVIDE([Prêts en défaut], [Prêts])", "0.0%"),
    ("fct_loans", "Montant en défaut (CZK)", "SUM(fct_loans[amount_in_default])", "#,0"),
    (
        "fct_loans",
        "Solde minimum moyen avant prêt (CZK)",
        "AVERAGE(fct_loans[min_balance_before])",
        "#,0",
    ),
    (
        "fct_loans",
        "Solde moyen avant prêt (CZK)",
        "AVERAGE(fct_loans[avg_month_end_balance_before])",
        "#,0",
    ),
    (
        "fct_loans",
        "Jours à découvert avant prêt (moyenne)",
        "AVERAGE(fct_loans[n_days_negative_balance_before])",
        "0.00",
    ),
    (
        "fct_loans",
        "Pénalités avant prêt (moyenne)",
        "AVERAGE(fct_loans[n_penalty_interest_before])",
        "0.00",
    ),
    ("fct_cards", "Cartes", "COUNTROWS(fct_cards)", "#,0"),
    ("check_results", "Contrôles", "COUNTROWS(check_results)", "#,0"),
    (
        "check_results",
        "Contrôles à surveiller",
        'CALCULATE(COUNTROWS(check_results), check_results[status] IN {"warning", "error"})',
        "#,0",
    ),
]

SORT_BY = {("fct_loans", "amount_band"): "amount_band_order"}

PG_TO_TMDL = {
    "bigint": "int64",
    "integer": "int64",
    "smallint": "int64",
    "numeric": "double",
    "double precision": "double",
    "real": "double",
    "text": "string",
    "character varying": "string",
    "date": "dateTime",
    "timestamp without time zone": "dateTime",
    "timestamp with time zone": "dateTime",
    "boolean": "boolean",
}


def tag(*parts: str) -> str:
    """Stable lineage tag: the same name always gives the same id (clean Git diffs)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "bank_dw/" + "/".join(parts)))


def quote(name: str) -> str:
    """TMDL name: quoted when it has spaces or special characters."""
    return name if name.replace("_", "").isalnum() and name.isascii() else f"'{name}'"


def read_columns() -> dict[str, list[tuple[str, str]]]:
    conn = psycopg2.connect(**connection_settings())
    try:
        with conn.cursor() as cur:
            columns = {}
            for table, schema, source in TABLES:
                cur.execute(
                    "select column_name, data_type from information_schema.columns"
                    " where table_schema = %s and table_name = %s order by ordinal_position",
                    (schema, source),
                )
                rows = cur.fetchall()
                if not rows:
                    raise SystemExit(
                        f"{schema}.{source} not found: run dbt build and the quality report"
                    )
                columns[table] = rows
            return columns
    finally:
        conn.close()


# --- semantic model (TMDL) -----------------------------------------------------------------


def table_tmdl(table: str, schema: str, source: str, columns: list[tuple[str, str]]) -> str:
    keys = {right.split(".")[1] for _, right in RELATIONSHIPS if right.startswith(table + ".")}
    lines = [f"table {table}", f"\tlineageTag: {tag(table)}"]
    if table == "dim_date":
        lines.append("\tdataCategory: Time")
    lines.append("")
    for home_table, measure_name, expression, fmt in MEASURES:
        if home_table != table:
            continue
        lines += [
            f"\tmeasure {quote(measure_name)} = {expression}",
            f"\t\tformatString: {fmt}",
            f"\t\tlineageTag: {tag(table, 'measure', measure_name)}",
            "",
        ]
    for column, pg_type in columns:
        data_type = PG_TO_TMDL[pg_type]
        lines.append(f"\tcolumn {column}")
        lines.append(f"\t\tdataType: {data_type}")
        if column in keys:
            lines.append("\t\tisKey")
        if data_type == "dateTime":
            lines.append("\t\tformatString: yyyy-mm-dd")
        elif data_type == "int64":
            lines.append("\t\tformatString: 0")
        elif data_type == "double":
            lines.append("\t\tformatString: #,0.00")
        # amounts can be summed; ids, codes and calendar numbers must not be
        not_additive = ("_id", "_order", "year", "month", "quarter", "day_of_week", "_months")
        is_number = data_type in ("int64", "double")
        summarize = "sum" if is_number and not column.endswith(not_additive) else "none"
        lines.append(f"\t\tsummarizeBy: {summarize}")
        lines.append(f"\t\tsourceColumn: {column}")
        lines.append(f"\t\tlineageTag: {tag(table, column)}")
        if (table, column) in SORT_BY:
            lines.append(f"\t\tsortByColumn: {SORT_BY[(table, column)]}")
        lines.append("")
    lines += [
        f"\tpartition {table} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        "\t\t\t\t    Source = PostgreSQL.Database(Server, Database),",
        f'\t\t\t\t    Data = Source{{[Schema="{schema}", Item="{source}"]}}[Data]',
        "\t\t\t\tin",
        "\t\t\t\t    Data",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_semantic_model(columns: dict[str, list[tuple[str, str]]]) -> None:
    base = OUT / f"{NAME}.SemanticModel"
    definition = base / "definition"
    (definition / "tables").mkdir(parents=True)
    (base / "definition.pbism").write_text(
        json.dumps(
            {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
                "version": "4.2",
                "settings": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (definition / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n", "utf-8")
    model = [
        "model Model",
        "\tculture: fr-FR",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: fr-FR",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
    ] + [f"ref table {table}" for table, _, _ in TABLES]
    (definition / "model.tmdl").write_text("\n".join(model) + "\n", encoding="utf-8")
    expressions = [
        "/// Server and port of the PostgreSQL warehouse (docker compose)",
        'expression Server = "localhost:5434" meta [IsParameterQuery=true, Type="Text", '
        "IsParameterQueryRequired=true]",
        f"\tlineageTag: {tag('expression', 'Server')}",
        "",
        "\tannotation PBI_ResultType = Text",
        "",
        "/// Name of the warehouse database",
        'expression Database = "bank_dw" meta [IsParameterQuery=true, Type="Text", '
        "IsParameterQueryRequired=true]",
        f"\tlineageTag: {tag('expression', 'Database')}",
        "",
        "\tannotation PBI_ResultType = Text",
        "",
    ]
    (definition / "expressions.tmdl").write_text("\n".join(expressions), encoding="utf-8")
    for table, schema, source in TABLES:
        (definition / "tables" / f"{table}.tmdl").write_text(
            table_tmdl(table, schema, source, columns[table]), encoding="utf-8"
        )
    rels = []
    for left, right in RELATIONSHIPS:
        rels += [
            f"relationship {tag('rel', left, right)}",
            f"\tfromColumn: {left}",
            f"\ttoColumn: {right}",
            "",
        ]
    (definition / "relationships.tmdl").write_text("\n".join(rels), encoding="utf-8")


# --- report (PBIR) --------------------------------------------------------------------------


def column(table: str, name: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def measure(table: str, name: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def projection(field: dict) -> dict:
    kind = "Column" if "Column" in field else "Measure"
    ref = f"{field[kind]['Expression']['SourceRef']['Entity']}.{field[kind]['Property']}"
    return {"field": field, "queryRef": ref, "nativeQueryRef": field[kind]["Property"]}


def literal(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def visual(
    name: str,
    visual_type: str,
    pos: tuple[int, int, int, int],
    roles: dict[str, list[dict]],
    title: str | None = None,
    sort: tuple[dict, str] | None = None,
    objects: dict | None = None,
) -> dict:
    x, y, w, h = pos
    query = {
        "queryState": {
            role: {"projections": [projection(f) for f in fields]} for role, fields in roles.items()
        }
    }
    if sort:
        query["sortDefinition"] = {
            "sort": [{"field": sort[0], "direction": sort[1]}],
            "isDefaultSort": False,
        }
    body = {"visualType": visual_type, "query": query}
    if objects:
        body["objects"] = objects
    if title:
        body["visualContainerObjects"] = {
            "title": [{"properties": {"show": literal("true"), "text": literal(f"'{title}'")}}]
        }
    return {
        "$schema": f"{SCHEMA_URL}/visualContainer/2.4.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 0, "width": w, "height": h},
        "visual": body,
    }


def pages() -> list[tuple[str, str, list[dict]]]:
    """The 3 pages: (id, title, visuals). Page size 1280 x 720."""
    month = column("dim_date", "month_start")
    cards = [
        ("Clients", measure("dim_client", "Clients")),
        ("Comptes", measure("dim_account", "Comptes")),
        ("Transactions", measure("fct_transactions", "Transactions")),
        ("Prêts", measure("fct_loans", "Prêts")),
        ("Taux de défaut", measure("fct_loans", "Taux de défaut")),
        ("Contrôles qualité à surveiller", measure("check_results", "Contrôles à surveiller")),
    ]
    overview = [
        visual(f"ov_card{i}", "card", (20 + i * 208, 20, 196, 110), {"Values": [m]}, title)
        for i, (title, m) in enumerate(cards)
    ]
    overview += [
        visual(
            "ov_volumes",
            "lineChart",
            (20, 150, 820, 550),
            {
                "Category": [month],
                "Y": [
                    measure("fct_transactions", "Volume crédits (CZK)"),
                    measure("fct_transactions", "Volume débits (CZK)"),
                ],
            },
            "Volume mensuel des transactions (CZK)",
        ),
        visual(
            "ov_quality",
            "pivotTable",
            (860, 150, 400, 550),
            {
                "Rows": [column("check_results", "family")],
                "Columns": [column("check_results", "status")],
                "Values": [measure("check_results", "Contrôles")],
            },
            "Rapport qualité : contrôles par statut",
        ),
    ]
    clients = [
        visual(
            "cl_region_slicer",
            "slicer",
            (20, 20, 240, 330),
            {"Values": [column("dim_client", "region")]},
            "Région du client",
        ),
        visual(
            "cl_region",
            "clusteredBarChart",
            (280, 20, 480, 330),
            {"Category": [column("dim_client", "region")], "Y": [measure("dim_client", "Clients")]},
            "Clients par région",
        ),
        visual(
            "cl_age",
            "clusteredColumnChart",
            (780, 20, 480, 330),
            {
                "Category": [column("dim_client", "age_band")],
                "Y": [measure("dim_client", "Clients")],
                "Series": [column("dim_client", "gender")],
            },
            "Clients par âge (au 31/12/1998) et sexe",
            sort=(column("dim_client", "age_band"), "Ascending"),
        ),
        visual(
            "cl_balance",
            "lineChart",
            (20, 370, 600, 330),
            {
                "Category": [month],
                "Y": [measure("fct_monthly_balance", "Solde moyen fin de mois (CZK)")],
            },
            "Solde moyen de fin de mois (CZK)",
        ),
        visual(
            "cl_cards",
            "columnChart",
            (640, 370, 620, 330),
            {
                "Category": [column("dim_client", "age_band")],
                "Y": [measure("fct_cards", "Cartes")],
                "Series": [column("fct_cards", "card_type")],
            },
            "Cartes par type et par âge du titulaire",
            sort=(column("dim_client", "age_band"), "Ascending"),
        ),
    ]
    risk_cards = [
        ("Prêts", measure("fct_loans", "Prêts")),
        ("Prêts en défaut", measure("fct_loans", "Prêts en défaut")),
        ("Taux de défaut", measure("fct_loans", "Taux de défaut")),
        ("Montant en défaut (CZK)", measure("fct_loans", "Montant en défaut (CZK)")),
    ]
    risk = [
        visual(f"rk_card{i}", "card", (20 + i * 250, 20, 235, 100), {"Values": [m]}, title)
        for i, (title, m) in enumerate(risk_cards)
    ]
    risk += [
        visual(
            "rk_status_slicer",
            "slicer",
            (1020, 20, 240, 200),
            {"Values": [column("fct_loans", "loan_status")]},
            "Statut du prêt",
        ),
        visual(
            "rk_duration",
            "clusteredColumnChart",
            (20, 140, 485, 270),
            {
                "Category": [column("fct_loans", "duration_months")],
                "Y": [measure("fct_loans", "Taux de défaut")],
            },
            "Taux de défaut par durée (mois)",
            sort=(column("fct_loans", "duration_months"), "Ascending"),
            # durations are 12, 24, 36, 48, 60: one bar each, not a continuous axis
            objects={"categoryAxis": [{"properties": {"axisType": literal("'Categorical'")}}]},
        ),
        visual(
            "rk_amount",
            "clusteredColumnChart",
            (520, 140, 485, 270),
            {
                "Category": [column("fct_loans", "amount_band")],
                "Y": [measure("fct_loans", "Taux de défaut")],
            },
            "Taux de défaut par montant du prêt (CZK)",
            sort=(column("fct_loans", "amount_band"), "Ascending"),
        ),
        visual(
            "rk_region",
            "clusteredBarChart",
            (1020, 240, 240, 460),
            {
                "Category": [column("dim_district", "region")],
                "Y": [measure("fct_loans", "Taux de défaut")],
            },
            "Taux de défaut par région de l'agence",
        ),
        visual(
            "rk_before",
            "tableEx",
            (20, 430, 985, 270),
            {
                "Values": [
                    column("fct_loans", "loan_status"),
                    measure("fct_loans", "Prêts"),
                    measure("fct_loans", "Solde moyen avant prêt (CZK)"),
                    measure("fct_loans", "Solde minimum moyen avant prêt (CZK)"),
                    measure("fct_loans", "Jours à découvert avant prêt (moyenne)"),
                    measure("fct_loans", "Pénalités avant prêt (moyenne)"),
                ]
            },
            "Indicateurs connus AVANT le prêt, par statut (sans fuite de données)",
        ),
    ]
    return [
        ("overview", "Vue d'ensemble", overview),
        ("clients", "Clients & activité", clients),
        ("risk", "Risque crédit", risk),
    ]


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_report() -> None:
    base = OUT / f"{NAME}.Report"
    write_json(
        base / "definition.pbir",
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}},
        },
    )
    definition = base / "definition"
    write_json(
        definition / "version.json",
        {"$schema": f"{SCHEMA_URL}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"},
    )
    write_json(
        definition / "report.json",
        {"$schema": f"{SCHEMA_URL}/report/3.0.0/schema.json", "themeCollection": {}},
    )
    all_pages = pages()
    write_json(
        definition / "pages" / "pages.json",
        {
            "$schema": f"{SCHEMA_URL}/pagesMetadata/1.0.0/schema.json",
            "pageOrder": [page_id for page_id, _, _ in all_pages],
            "activePageName": all_pages[0][0],
        },
    )
    for page_id, title, visuals in all_pages:
        folder = definition / "pages" / page_id
        write_json(
            folder / "page.json",
            {
                "$schema": f"{SCHEMA_URL}/page/2.0.0/schema.json",
                "name": page_id,
                "displayName": title,
                "displayOption": "FitToPage",
                "height": 720,
                "width": 1280,
            },
        )
        for v in visuals:
            write_json(folder / "visuals" / v["name"] / "visual.json", v)


def main() -> int:
    existing = [f for f in (OUT / f"{NAME}.SemanticModel", OUT / f"{NAME}.Report") if f.exists()]
    if existing:
        # Once opened and saved, the files belong to Power BI Desktop (it adds its own
        # settings and cache): we never overwrite them. Delete the folders by hand to restart.
        print(f"{OUT} already contains the project: nothing written (edit it in Power BI Desktop).")
        return 1
    columns = read_columns()
    write_semantic_model(columns)
    write_report()
    write_json(
        OUT / f"{NAME}.pbip",
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        },
    )
    n_visuals = sum(len(v) for _, _, v in pages())
    print(
        f"Power BI project written in {OUT}: {len(TABLES)} tables, {len(MEASURES)} measures, "
        f"{len(RELATIONSHIPS)} relationships, 3 pages, {n_visuals} visuals."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
