from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import openfoam.solve.checkpoint as checkpoint


def _times(case: Path, names: tuple[str, ...]) -> None:
    for processor in ("processor0", "processor1"):
        for name in names:
            for region in checkpoint.REGIONS:
                path = case / processor / name / region
                path.mkdir(parents=True)
                (path / "T").write_text("field")


def _directories(processor: Path) -> set[str]:
    return {path.name for path in processor.iterdir() if path.is_dir()}


def test_prune_only_processor_numeric_directories(tmp_path):
    case = tmp_path / "case"
    _times(case, ("0", "5", "10", "15", "constant", "system", "notes"))
    for name in ("0", "5", "constant", "system", "notes"):
        path = case / name / "sentinel"
        path.parent.mkdir(parents=True)
        path.write_text(name)
    for processor in ("processor0", "processor1"):
        (case / processor / "7").write_text("numeric file")
    untouched = {
        path: path.read_bytes() for path in case.rglob("*")
        if path.is_file() and not any(
            path.is_relative_to(case / processor / "5")
            for processor in ("processor0", "processor1")
        )
    }

    deleted = checkpoint.prune_time_directories(case, [
        {"iter": 15, "timeName": "15"}, {"iter": 10, "timeName": "10"},
    ])

    assert set(deleted) == {"processor0/5", "processor1/5"}
    for processor in ("processor0", "processor1"):
        assert _directories(case / processor) == {"0", "10", "15", "constant", "system", "notes"}
    for path, content in untouched.items():
        assert path.read_bytes() == content


def test_prune_keeps_undeclared_latest_but_removes_intermediate_times(tmp_path):
    case = tmp_path / "case"
    _times(case, ("0", "5", "10", "12", "1.25e1", "15", "20"))

    deleted = checkpoint.prune_time_directories(case, [
        {"iter": 10, "timeName": "10"}, {"iter": 15, "timeName": "15"},
    ])

    assert set(deleted) == {f"processor{index}/{name}" for index in range(2) for name in ("5", "12", "1.25e1")}
    for index in range(2):
        assert _directories(case / f"processor{index}") == {"0", "10", "15", "20"}


def test_prune_preserves_latest_complete_candidate_and_newer_partial_time(tmp_path):
    case = tmp_path / "case"
    _times(case, ("0", "5", "10", "15", "20"))
    (case / "processor0/25/hot").mkdir(parents=True)
    assert checkpoint._latest_time(case) == (20, 20.0, "20")

    checkpoint.prune_time_directories(case, [
        {"iter": 10, "timeName": "10"}, {"iter": 15, "timeName": "15"},
    ])

    assert _directories(case / "processor0") == {"0", "10", "15", "20", "25"}
    assert _directories(case / "processor1") == {"0", "10", "15", "20"}


def test_prune_continues_after_missing_directory_and_oserror(tmp_path, monkeypatch):
    case = tmp_path / "case"
    _times(case, ("0", "5", "6", "7", "10", "15"))
    original = shutil.rmtree
    attempted = set()

    def remove(path):
        relative = path.relative_to(case).as_posix()
        attempted.add(relative)
        if relative == "processor0/5":
            original(path)
            raise FileNotFoundError("concurrent removal")
        if relative == "processor0/6":
            raise OSError("injected removal failure")
        original(path)

    monkeypatch.setattr(checkpoint.shutil, "rmtree", remove)
    declarations = [{"iter": 10, "timeName": "10"}, {"iter": 15, "timeName": "15"}]
    deleted = checkpoint.prune_time_directories(case, declarations)

    assert attempted == {f"processor{index}/{name}" for index in range(2) for name in ("5", "6", "7")}
    assert set(deleted) == attempted - {"processor0/5", "processor0/6"}
    assert _directories(case / "processor0") == {"0", "6", "10", "15"}
    assert _directories(case / "processor1") == {"0", "10", "15"}
    monkeypatch.setattr(checkpoint.shutil, "rmtree", original)
    assert checkpoint.prune_time_directories(case, declarations) == ["processor0/6"]
    assert checkpoint.prune_time_directories(case, declarations) == []


@pytest.mark.parametrize("keep", [2, 3])
def test_checkpoint_keep_controls_tar_and_time_rotation(tmp_path, monkeypatch, keep):
    case, output = tmp_path / "case", tmp_path / "output"
    _times(case, ("0", "5", "10", "15", "20"))
    monkeypatch.setattr(checkpoint, "CHECKPOINT_KEEP", keep)
    for iteration in (15, 5, 10, 20):
        checkpoint.produce_checkpoint(
            case, output, spec_hash="a" * 64, decomp_n=2,
            iteration=iteration, phys_t=float(iteration), time_name=str(iteration),
        )

    declarations = checkpoint._read_declarations(output / "checkpoints.ndjson")
    checkpoint.prune_time_directories(case, declarations)

    expected = {"15", "20"} if keep == 2 else {"10", "15", "20"}
    assert len(declarations) == 4
    assert {path.name for path in (output / "checkpoints").glob("*.tar")} == {
        f"checkpoint-{name}.tar" for name in expected
    }
    for index in range(2):
        assert _directories(case / f"processor{index}") == {"0"} | expected
