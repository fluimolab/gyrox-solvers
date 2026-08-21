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
