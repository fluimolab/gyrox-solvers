from __future__ import annotations

import json

from openfoam.common.progress import ProgressWriter
from openfoam.solve.runner import SolverOutputParser


def test_live_time_and_residual_lines_emit_schema_valid_ndjson(capsys):
    parser = SolverOutputParser(ProgressWriter(), 550)
    parser.consume("Time = 20\n")
    parser.consume("Solving for fluid region hot\n")
    parser.consume("smoothSolver: Solving for p_rgh, Initial residual = 0.012, Final residual = 0.001, No Iterations 1\n")
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(event["type"], event["payload"]["iter"]) for event in events] == [("progress", 20), ("residual", 20)]
    assert events[1]["payload"] == {"iter": 20, "region": "hot", "field": "p_rgh", "initial": 0.012}
