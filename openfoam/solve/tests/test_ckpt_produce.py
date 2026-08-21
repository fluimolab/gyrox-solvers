from __future__ import annotations

import json
import tarfile

import pytest

from openfoam.solve.checkpoint import produce_checkpoint


@pytest.mark.parametrize("_case", [None], ids=["[SR-09]"])
def test_checkpoint_product(_case, tmp_path):
    case, output = tmp_path / "case", tmp_path / "output"
    for processor in ("processor0", "processor1"):
        for region in ("hot", "cold", "solid"):
            path = case / processor / "10" / region
            path.mkdir(parents=True)
            (path / "T").write_text("field")
    declaration = produce_checkpoint(case, output, spec_hash="a" * 64, decomp_n=2, iteration=10, phys_t=10.0, time_name="10")
    assert set(declaration) == {"path", "bytes", "sha256", "iter", "physT", "specHash", "decompN", "timeName", "regions"}
    with tarfile.open(output / declaration["path"]) as archive:
        assert any(name.startswith("processor0/10/hot") for name in archive.getnames())
    assert json.loads((output / "checkpoints.ndjson").read_text()) == declaration


def test_duplicate_iteration_and_in_progress_snapshot_are_rejected(tmp_path):
    case, output = tmp_path / "case", tmp_path / "output"
    for region in ("hot", "cold", "solid"):
        path = case / "processor0/10" / region
        path.mkdir(parents=True)
        (path / "T").write_text("complete")
    first = produce_checkpoint(case, output, spec_hash="a" * 64, decomp_n=1, iteration=10, phys_t=10.0, time_name="10")
    original = (output / first["path"]).read_bytes()
    with pytest.raises(FileExistsError, match="already declared"):
        produce_checkpoint(case, output, spec_hash="a" * 64, decomp_n=1, iteration=10, phys_t=10.0, time_name="10")
    assert (output / first["path"]).read_bytes() == original
    assert len((output / "checkpoints.ndjson").read_text().splitlines()) == 1

    (case / "processor0/11/hot").mkdir(parents=True)
    (case / "processor0/11/cold").mkdir(parents=True)
    (case / "processor0/11/solid").mkdir(parents=True)
    (case / "processor0/11/hot/T.part").write_text("writing")
    with pytest.raises(ValueError, match="write is incomplete"):
        produce_checkpoint(case, output, spec_hash="a" * 64, decomp_n=1, iteration=11, phys_t=11.0, time_name="11")
    assert not (output / "checkpoints/checkpoint-11.tar").exists()
