from __future__ import annotations

import json

import pytest

from openfoam.common.progress import ProgressWriter
from openfoam.solve.judge import judge_rows
from openfoam.solve.runner import _summary, map_process_exit
from openfoam.solve.tests.conftest import stable_rows


@pytest.mark.parametrize("_case", [None], ids=["[SR-08]"])
def test_exit_and_dual_record(_case, convergence, capsys):
    assert [map_process_exit(code, oom_killed=(code == 137)) for code in (0, 10, 20, 30, 40, 137)] == [
        ("SUCCEEDED", 0), ("SUCCEEDED_WITH_WARNINGS", 10), ("PHYSICS_DIVERGED", 20),
        ("INVALID_INPUT", 30), ("INVALID_INPUT", 40), ("RESOURCE_EXHAUSTED", 137),
    ]
    judgment = judge_rows(stable_rows(), convergence)
    summary = _summary(judgment, wall_clock=1.0, cells=3)
    ProgressWriter().emit("phase", {"name": "extract"}, outcome=judgment.outcome, converged=summary["converged"])
    event = json.loads(capsys.readouterr().out)
    assert event["outcome"] == judgment.outcome
    assert event["converged"] == summary["converged"]
