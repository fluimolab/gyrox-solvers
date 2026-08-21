# ADR-006 D8-2: QoI identifiers below are contract data, not new verification claims.
from __future__ import annotations

import json

import pytest

from openfoam.solve.runner import verification_defaults


@pytest.mark.parametrize("_case", [None], ids=["[SR-12]"])
def test_conservative_verification(_case, repo_root):
    fixture = json.loads((repo_root / "contract/fixtures/verification/runner-base.json").read_text())
    assert verification_defaults() == fixture["value"]["verification"]
    assert verification_defaults()["dpHot"]["status"] == "regression_only"
