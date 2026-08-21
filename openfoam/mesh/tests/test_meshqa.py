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
    for region in ("hot", "cold", "solid"):
        poly_mesh = tmp_path / f"constant/{region}/polyMesh"
        (poly_mesh / "sets").mkdir(parents=True)
        (poly_mesh / "sets/region0").write_text("checkMesh scratch")
        (tmp_path / f"constant/{region}/cellToRegion").write_text("checkMesh scratch")
    qa = {f"criterion{index}": index for index in range(13)}
    completed = subprocess.CompletedProcess([], 0, stdout="Mesh OK.\nEnd")
    verdict = run_check_mesh(tmp_path, qa, run=lambda *args, **kwargs: completed)
    assert verdict["command"] == ["checkMesh", "-allRegions", "-meshQuality"]
    assert verdict["passed"] is True
    assert all(item["violations"] == 0 for item in verdict["checks"].values())
    control_dict = (tmp_path / "system/controlDict").read_text()
    for required_entry in (
        "application checkMesh;",
        "startFrom startTime;",
        "stopAt endTime;",
        "deltaT 1;",
        "writeControl timeStep;",
        "writeInterval 1;",
        "runTimeModifiable false;",
    ):
        assert required_entry in control_dict
    for region in ("hot", "cold", "solid"):
        region_system = tmp_path / "system" / region
        quality = (region_system / "meshQualityDict").read_text()
        assert "meshQualityControls" not in quality
        assert "criterion0" in quality and "0;" in quality
        assert (region_system / "fvSchemes").is_file()
        assert (region_system / "fvSolution").is_file()
        assert not (tmp_path / f"constant/{region}/cellToRegion").exists()
        assert not (tmp_path / f"constant/{region}/polyMesh/sets").exists()
