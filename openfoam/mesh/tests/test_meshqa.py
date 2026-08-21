from __future__ import annotations

import subprocess

import pytest

from openfoam.mesh.qa import run_check_mesh, violation_count


@pytest.mark.parametrize("_case", [None], ids=["[MC-04]"])
def test_quality_verdict(_case, tmp_path):
    assert violation_count("Mesh OK.\nEnd", 0) == 0
    assert violation_count("Failed 2 mesh checks", 0) == 2
    assert violation_count("Mesh OK", 1) == 1
    assert violation_count("***Error in mesh", 0) == 1
    qa = {f"criterion{index}": index for index in range(13)}
    completed = subprocess.CompletedProcess([], 0, stdout="Mesh OK.\nEnd")
    verdict = run_check_mesh(tmp_path, qa, run=lambda *args, **kwargs: completed)
    assert verdict["command"] == ["checkMesh", "-allRegions", "-meshQuality"]
    assert verdict["passed"] is True
    assert all(item["violations"] == 0 for item in verdict["checks"].values())
