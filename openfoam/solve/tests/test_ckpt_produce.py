from __future__ import annotations

import json
import tarfile

import pytest

import openfoam.solve.checkpoint as checkpoint
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


def test_declaration_append_failure_is_retryable(tmp_path, monkeypatch):
    case, output = tmp_path / "case", tmp_path / "output"
    for region in ("hot", "cold", "solid"):
        path = case / "processor0/10" / region
        path.mkdir(parents=True)
        (path / "T").write_text("complete")

    original = checkpoint._replace_declarations
    calls = 0

    def fail_once(path, declarations):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected declaration append failure")
        original(path, declarations)

    monkeypatch.setattr(checkpoint, "_replace_declarations", fail_once)
    with pytest.raises(OSError, match="injected"):
        produce_checkpoint(
            case, output, spec_hash="a" * 64, decomp_n=1,
            iteration=10, phys_t=10.0, time_name="10",
        )
    assert not (output / "checkpoints/checkpoint-10.tar").exists()
    assert not (output / "checkpoints.ndjson").exists()

    declaration = produce_checkpoint(
        case, output, spec_hash="a" * 64, decomp_n=1,
        iteration=10, phys_t=10.0, time_name="10",
    )
    assert (output / declaration["path"]).is_file()
    assert len((output / "checkpoints.ndjson").read_text().splitlines()) == 1


def _produce_iteration(case, output, iteration):
    for region in ("hot", "cold", "solid"):
        path = case / "processor0" / str(iteration) / region
        path.mkdir(parents=True)
        (path / "T").write_text("complete")
    return produce_checkpoint(
        case, output, spec_hash="a" * 64, decomp_n=1,
        iteration=iteration, phys_t=float(iteration), time_name=str(iteration),
    )


def test_checkpoint_rotation_keeps_two_highest_iterations_and_all_declarations(tmp_path):
    case, output = tmp_path / "case", tmp_path / "output"
    declarations = []
    for iteration in (10, 30, 20, 40):
        declarations.append(_produce_iteration(case, output, iteration))
        retained = sorted(declarations, key=lambda item: item["iter"], reverse=True)[:2]
        assert {path.name for path in (output / "checkpoints").glob("*.tar")} == {
            f"checkpoint-{item['iter']}.tar" for item in retained
        }
        assert [json.loads(line) for line in (output / "checkpoints.ndjson").read_text().splitlines()] == declarations


def test_checkpoint_rotation_tolerates_already_removed_tar(tmp_path):
    case, output = tmp_path / "case", tmp_path / "output"
    first = _produce_iteration(case, output, 10)
    _produce_iteration(case, output, 20)
    (output / first["path"]).unlink()
    _produce_iteration(case, output, 30)
    assert {path.name for path in (output / "checkpoints").glob("*.tar")} == {"checkpoint-20.tar", "checkpoint-30.tar"}
    assert len((output / "checkpoints.ndjson").read_text().splitlines()) == 3


def test_failed_checkpoint_declaration_does_not_rotate_existing_tars(tmp_path, monkeypatch):
    case, output = tmp_path / "case", tmp_path / "output"
    _produce_iteration(case, output, 10)
    _produce_iteration(case, output, 20)
    original = (output / "checkpoints.ndjson").read_bytes()

    def fail(path, declarations):
        raise OSError("injected declaration failure")

    monkeypatch.setattr(checkpoint, "_replace_declarations", fail)
    with pytest.raises(OSError, match="injected"):
        _produce_iteration(case, output, 30)
    assert {path.name for path in (output / "checkpoints").glob("*.tar")} == {"checkpoint-10.tar", "checkpoint-20.tar"}
    assert (output / "checkpoints.ndjson").read_bytes() == original
