from __future__ import annotations

import json

import pytest


TOKENS = ("dpHotPa", "dpColdPa", "qW", "qHotW", "qColdW", "ntu", "uaWK", "jFactor", "fFactor", "tOutHotK", "tOutColdK", "energyBalancePct")


@pytest.mark.parametrize("_case", [None], ids=["[MC-05]"])
def test_diagnostic_vocabulary_scan(_case):
    fixture_path = __import__("pathlib").Path(__file__).resolve().parents[3] / "contract/fixtures/convert-report/accept/phase-c-cl2.json"
    diagnostics = json.loads(fixture_path.read_text(encoding="utf-8"))["diagnostics"]
    encoded = json.dumps(diagnostics, sort_keys=True)
    assert not any(token in encoded for token in TOKENS)
