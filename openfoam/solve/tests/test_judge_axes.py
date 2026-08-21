# ADR-006 D8-2: QoI identifiers below are contract data, not new verification claims.
from __future__ import annotations

import json

import pytest

from openfoam.solve.judge import judge_csv


@pytest.mark.m0_assets
@pytest.mark.parametrize("_case", [None], ids=["[SR-05]"])
def test_axis_separation(_case, gyrox_root, convergence):
    golden_path = gyrox_root / "m0/m04/runs/phaseC/results/window-analysis-C.json"
    golden = json.loads(golden_path.read_text())["levels"]["CL2"]
    result = judge_csv(gyrox_root / "m0/m04/runs/phaseC/results/extract/CL2/timeseries.csv", convergence, max_iter=golden["nIters"])
    assert result.window_mean_converged is False
    assert result.dp_hot_window_mean_converged is True
    assert result.converged is False
