from __future__ import annotations

import json

import pytest

from openfoam.common.schema_adapter import validate


@pytest.mark.parametrize("_case", [None], ids=["[MC-02]"])
def test_report_schema(_case):
    fixture = json.loads((
        __import__("pathlib").Path(__file__).resolve().parents[3]
        / "contract/fixtures/convert-report/accept/phase-c-cl2.json"
    ).read_text(encoding="utf-8"))
    validate("convert-report", fixture)
    assert set(fixture) == {"schemaVersion", "meshCombinedSha", "portAreasM2", "diagnostics"}
