from __future__ import annotations

import pytest

from openfoam.solve.judge import judge_rows
from openfoam.solve.tests.conftest import stable_rows


@pytest.mark.parametrize("_case", [None], ids=["[SR-06]"])
def test_insufficient_fails_closed(_case, convergence):
    result = judge_rows(stable_rows(399), convergence)
    assert result.exit_code == 10
    assert result.axes["insufficient"] is True
    assert result.converged is result.window_mean_converged is result.dp_hot_window_mean_converged is False
