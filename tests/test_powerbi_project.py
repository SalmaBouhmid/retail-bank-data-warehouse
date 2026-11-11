"""Consistency of the Power BI project (powerbi/), without Power BI Desktop:
every field used by a visual or a relationship must exist in the semantic model."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "powerbi" / "BankDW.SemanticModel" / "definition"
REPORT = ROOT / "powerbi" / "BankDW.Report" / "definition"

pytestmark = pytest.mark.skipif(not MODEL.exists(), reason="Power BI project not generated")


def _model() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """{table: columns}, {table: measures} read from the TMDL files."""
    columns, measures = {}, {}
    for path in (MODEL / "tables").glob("*.tmdl"):
        text = path.read_text(encoding="utf-8")
        table = re.search(r"^table (\w+)", text, re.M).group(1)
        columns[table] = set(re.findall(r"^\tcolumn (\w+)", text, re.M))
        measures[table] = {
            m.strip("'") for m in re.findall(r"^\tmeasure ('[^']+'|\w+) =", text, re.M)
        }
    return columns, measures


def _fields(node, found):
    """All Column / Measure references inside a visual.json."""
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            if kind in node and isinstance(node[kind], dict) and "Property" in node[kind]:
                found.append(
                    (kind, node[kind]["Expression"]["SourceRef"]["Entity"], node[kind]["Property"])
                )
        for value in node.values():
            _fields(value, found)
    elif isinstance(node, list):
        for value in node:
            _fields(value, found)
    return found


def test_every_visual_field_exists_in_the_model():
    columns, measures = _model()
    visuals = list(REPORT.glob("pages/*/visuals/*/visual.json"))
    assert len(visuals) >= 3
    for path in visuals:
        for kind, table, name in _fields(json.loads(path.read_text(encoding="utf-8")), []):
            known = columns if kind == "Column" else measures
            assert name in known.get(table, set()), f"{path.parent.name}: {table}[{name}]"


def test_relationships_use_existing_columns():
    columns, _ = _model()
    text = (MODEL / "relationships.tmdl").read_text(encoding="utf-8")
    pairs = re.findall(r"(?:fromColumn|toColumn): (\w+)\.(\w+)", text)
    assert len(pairs) >= 2
    for table, column in pairs:
        assert column in columns[table], f"{table}.{column}"


def test_report_has_the_3_pages_of_the_specification():
    pages = json.loads((REPORT / "pages" / "pages.json").read_text(encoding="utf-8"))
    names = [
        json.loads((REPORT / "pages" / p / "page.json").read_text(encoding="utf-8"))["displayName"]
        for p in pages["pageOrder"]
    ]
    assert names == ["Vue d'ensemble", "Clients & activité", "Risque crédit"]
