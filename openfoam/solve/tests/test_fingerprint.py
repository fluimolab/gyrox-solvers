from __future__ import annotations

import pytest

from openfoam.solve.renderer import render_case
from openfoam.solve.renderer.render import _fingerprint


@pytest.mark.parametrize("_case", [None], ids=["[SR-03]"])
def test_fingerprint_scope(_case, tmp_path, solve_document, patch_map):
    first = render_case(solve_document, patch_map, tmp_path / "a", cores=4, write_interval=100)
    second = render_case(solve_document, patch_map, tmp_path / "b", cores=16, write_interval=900)
    assert first.fingerprint == second.fingerprint
    solution = tmp_path / "b/system/hot/fvSolution"
    solution.write_text(solution.read_text().replace("U 0.3", "U 0.31"))
    assert _fingerprint(tmp_path / "b", solve_document["payload"]) != second.fingerprint


def test_fingerprint_reads_rendered_decomposition_and_precision(tmp_path, solve_document, patch_map):
    rendered = render_case(solve_document, patch_map, tmp_path / "case", cores=4, write_interval=100)
    root = tmp_path / "case/system/decomposeParDict"
    root.write_text(root.read_text().replace("method hierarchical", "method scotch"))
    with pytest.raises(ValueError, match="decomposition methods"):
        _fingerprint(tmp_path / "case", solve_document["payload"])

    render_case(solve_document, patch_map, tmp_path / "region", cores=4, write_interval=100)
    region = tmp_path / "region/system/hot/decomposeParDict"
    region.write_text(region.read_text().replace("method hierarchical", "method scotch"))
    with pytest.raises(ValueError, match="decomposition methods"):
        _fingerprint(tmp_path / "region", solve_document["payload"])

    render_case(solve_document, patch_map, tmp_path / "precision", cores=4, write_interval=100)
    control = tmp_path / "precision/system/controlDict"
    control.write_text(control.read_text().replace("writePrecision  10", "writePrecision  6"))
    assert _fingerprint(tmp_path / "precision", solve_document["payload"]) != rendered.fingerprint


@pytest.mark.parametrize("mutation", ["preload", "wall_heat_flux_hot_log", "wall_heat_flux_cold_log"])
def test_operational_contract_removal_fails_static_validation(mutation, tmp_path, solve_document, patch_map):
    case = tmp_path / mutation
    render_case(solve_document, patch_map, case, cores=4, write_interval=100)
    if mutation == "preload":
        path = case / "system/controlDict"
        path.write_text(path.read_text().replace('#include "functions.cfg"', ""))
    else:
        path = case / "system/functions.cfg"
        text = path.read_text()
        name = "whf_hot" if mutation == "wall_heat_flux_hot_log" else "whf_cold"
        start = text.index(name)
        log = text.index("log             yes", start)
        path.write_text(text[:log] + text[log:].replace("log             yes", "log             no", 1))
    with pytest.raises(ValueError):
        _fingerprint(case, solve_document["payload"])


@pytest.mark.parametrize("mutation", ["energy_coupling", "h_tolerance", "inlet_type"])
def test_remaining_rendered_operational_rules_are_fingerprinted(mutation, tmp_path, solve_document, patch_map):
    case = tmp_path / mutation
    baseline = render_case(solve_document, patch_map, case, cores=4, write_interval=100)
    if mutation == "energy_coupling":
        path = case / "system/fvSolution"
        path.write_text(path.read_text().replace("iterations      25", "iterations      24"))
    elif mutation == "h_tolerance":
        path = case / "system/fvSolution"
        path.write_text(path.read_text().replace("h           1e-06", "h           2e-06"))
    else:
        path = case / "0/hot/U"
        path.write_text(path.read_text().replace("flowRateInletVelocity", "fixedValue", 1))
    assert _fingerprint(case, solve_document["payload"]) != baseline.fingerprint
