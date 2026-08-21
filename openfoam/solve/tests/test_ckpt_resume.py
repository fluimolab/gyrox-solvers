from __future__ import annotations

import json

import pytest

from openfoam.solve.checkpoint import produce_checkpoint, resume_checkpoint


@pytest.mark.parametrize("_case", [None], ids=["[SR-10]"])
def test_resume_branches(_case, tmp_path):
    source, output, target = tmp_path / "source", tmp_path / "output", tmp_path / "target"
    for region in ("hot", "cold", "solid"):
        path = source / "processor0/5" / region
        path.mkdir(parents=True)
        (path / "T").write_text("field")
    declaration = produce_checkpoint(source, output, spec_hash="a" * 64, decomp_n=1, iteration=5, phys_t=5.0, time_name="5")
    archive, sidecar = output / declaration["path"], output / "checkpoint.meta.json"
    assert resume_checkpoint(target, archive, sidecar, spec_hash="a" * 64, decomp_n=1).action == "cold"
    sidecar.write_text(json.dumps(declaration))
    assert resume_checkpoint(target, archive, sidecar, spec_hash="b" * 64, decomp_n=1).exit_code == 40
    assert resume_checkpoint(target, archive, sidecar, spec_hash="a" * 64, decomp_n=2).action == "cold"
