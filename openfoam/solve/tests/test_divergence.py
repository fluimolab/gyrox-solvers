from __future__ import annotations

import math

import pytest

from openfoam.solve.judge import judge_rows
from openfoam.solve.tests.conftest import stable_rows


@pytest.mark.parametrize("_case", [None], ids=["[SR-07]"])
def test_precedence_branches(_case, convergence):
    nonfinite = stable_rows()
    nonfinite[0]["dpHotPa"] = math.nan
    wrong_sign = stable_rows()
    for row in wrong_sign[-200:]:
        row["qHotW"] = 1700.0
    blowup = stable_rows()
    for row in blowup[-200:]:
        row["residual_hot_U"] = 2.0
    for rows in (nonfinite, wrong_sign, blowup):
        result = judge_rows(rows, convergence)
        assert (result.exit_code, result.outcome) == (20, "PHYSICS_DIVERGED")
