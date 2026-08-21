from __future__ import annotations

import pytest

from openfoam.solve.renderer.render import OUTLET_BINDINGS, join_patches


@pytest.mark.parametrize("_case", [None], ids=["[SR-01]"])
def test_join_bindings(_case, solve_document, patch_map):
    joined = join_patches(solve_document["payload"], patch_map)
    assert joined["port:hot-inlet"] == "hot_inlet"
    assert joined["port:cold-inlet"] == "cold_inlet"
    assert {key: joined[key] for key in OUTLET_BINDINGS} == OUTLET_BINDINGS
