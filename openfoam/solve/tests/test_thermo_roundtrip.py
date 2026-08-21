from __future__ import annotations

import re

import pytest

from openfoam.solve.renderer import render_case


@pytest.mark.m0_assets
@pytest.mark.parametrize("_case", [None], ids=["[SR-02]"])
def test_material_roundtrip(_case, tmp_path, solve_document, patch_map, gyrox_root):
    render_case(solve_document, patch_map, tmp_path, cores=8, write_interval=500)
    for region, keys in {"hot": ("rho", "Cp", "mu", "Pr"), "cold": ("rho", "Cp", "mu", "Pr"), "solid": ("rho", "Cp", "kappa")}.items():
        actual = (tmp_path / "constant" / region / "thermophysicalProperties").read_text()
        original = (gyrox_root / "m0/m04/runs/phaseC/solve/CL2/constant" / region / "thermophysicalProperties").read_text()
        for key in keys:
            pattern = rf"\b{key}\s+([0-9.eE+-]+)\s*;"
            assert float(re.search(pattern, actual).group(1)) == float(re.search(pattern, original).group(1))
        for selector in ("type", "mixture", "transport", "thermo", "equationOfState", "specie", "energy"):
            assert re.search(rf"\b{selector}\s+\w+\s*;", actual).group(0) == re.search(rf"\b{selector}\s+\w+\s*;", original).group(0)
