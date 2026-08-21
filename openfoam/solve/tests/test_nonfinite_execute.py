from __future__ import annotations

import csv
import json

import pytest

import openfoam.solve.runner as runner
from openfoam.solve.tests.conftest import stable_rows
from openfoam.solve.tests.test_runner_integration import _prepare_work


@pytest.mark.parametrize("scenario", ["zero-rows", "partial-rows", "cell-count-failure"])
def test_nonfinite_execute_is_never_overwritten(
    scenario, repo_root, tmp_path, solve_document, patch_map, monkeypatch, capsys,
):
    work = _prepare_work(tmp_path, solve_document, patch_map)

    def decompose(case):
        for region in ("hot", "cold", "solid"):
            mesh = case / "processor0/constant" / region / "polyMesh"
            mesh.mkdir(parents=True, exist_ok=True)

    def extract(_case, output):
        path = output / "timeseries.csv"
        rows = [] if scenario == "zero-rows" else stable_rows(10)
        fieldnames = list(stable_rows(1)[0])
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    monkeypatch.setattr(runner, "_decompose", decompose)
    monkeypatch.setattr(runner, "_run_solver_with_checkpoints", lambda *args, **kwargs: 20)
    monkeypatch.setattr(runner, "_extract_timeseries", extract)
    if scenario == "cell-count-failure":
        monkeypatch.setattr(runner, "region_cell_count", lambda *_args: (_ for _ in ()).throw(ValueError("cell count unavailable")))
    else:
        monkeypatch.setattr(runner, "region_cell_count", lambda *_args: 1)

    assert runner.execute(work) == 20
    result = json.loads((work / "output/result.json").read_text())
    summary = json.loads((work / "output/summary.json").read_text())
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert (result["outcome"], result["exitCode"]) == ("PHYSICS_DIVERGED", 20)
    assert summary["convergenceAxes"]["finite"]["pass"] is False
    assert summary["convergenceAxes"]["insufficient"] is True
    assert summary["cells"] == (None if scenario == "cell-count-failure" else 3)
    assert events[-1]["outcome"] == "PHYSICS_DIVERGED"
