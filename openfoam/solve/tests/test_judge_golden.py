# ADR-006 D8-2: QoI identifiers below are contract data, not new verification claims.
from __future__ import annotations

import json

import pytest

from openfoam.solve.judge import judge_csv


@pytest.mark.m0_assets
@pytest.mark.parametrize("_case", [None], ids=["[SR-04]"])
def test_recorded_window(_case, gyrox_root, convergence):
    csv_path = gyrox_root / "m0/m04/runs/phaseC/results/extract/CL2/timeseries.csv"
    golden_path = gyrox_root / "m0/m04/runs/phaseC/results/window-analysis-C.json"
    if not csv_path.is_file() or not golden_path.is_file():
        pytest.fail(f"required Phase C record is absent: {csv_path} or {golden_path}", pytrace=False)
    golden = json.loads(golden_path.read_text())["levels"]["CL2"]
    result = judge_csv(csv_path, convergence, max_iter=golden["nIters"])
    key_map = {"dpHotPa": "dpHot", "dpColdPa": "dpCold", "qW": "qWall"}
    for current, recorded in key_map.items():
        axis = result.axes["perQoI"][current]
        expected = golden["qoi"][recorded]
        assert axis["drift"]["driftPct"] == expected["driftPct"]
        assert axis["span"]["spanPct"] == expected["spanPct"]
        assert axis["amplitudePct"] == expected["amplitudePct"]
    assert result.window_mean_converged is golden["gate_windowMean_pass"]
