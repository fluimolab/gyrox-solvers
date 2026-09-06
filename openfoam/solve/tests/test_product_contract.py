from __future__ import annotations

import csv
import io
import json
import subprocess
import tarfile

import pytest

from openfoam.common.io import sha256_file
from openfoam.common.schema_adapter import validate
from openfoam.mesh.convert import _package_mesh_case
import openfoam.solve.runner as runner
from openfoam.solve.tests.conftest import stable_rows
from openfoam.solve.tests.test_runner_integration import _prepare_work


def test_mesh_case_tar_accepts_worker_blob_name(tmp_path):
    source = tmp_path / "mesh"
    mesh = source / "constant/hot/polyMesh"
    mesh.mkdir(parents=True)
    (mesh / "points").write_bytes(b"binary mesh\x00\xff")
    (source / "system").mkdir()
    blob = tmp_path / ("mesh-mesh-case-" + "a" * 64)
    _package_mesh_case(source, blob)
    target = tmp_path / "case"
    runner._copy_mesh_case(blob, target)
    assert (target / "constant/hot/polyMesh/points").read_bytes() == (mesh / "points").read_bytes()
    assert (target / "system").is_dir()


def test_mesh_case_directory_input_remains_compatible(tmp_path):
    source, target = tmp_path / "mesh", tmp_path / "case"
    source.mkdir()
    target.mkdir()
    (source / "field").write_bytes(b"mesh")
    (target / "existing").write_text("keep")
    runner._copy_mesh_case(source, target)
    assert (target / "field").read_bytes() == b"mesh"
    assert (target / "existing").read_text() == "keep"


def test_mesh_case_tar_rejects_unsafe_members_before_extracting(tmp_path):
    for index, (name, kind) in enumerate((
        (str(tmp_path / "absolute"), tarfile.REGTYPE),
        ("../escape", tarfile.REGTYPE),
        ("nested/../inside", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("hardlink", tarfile.LNKTYPE),
        ("fifo", tarfile.FIFOTYPE),
    )):
        blob, target = tmp_path / f"blob-{index}", tmp_path / f"case-{index}"
        with tarfile.open(blob, "w") as archive:
            safe = tarfile.TarInfo("safe")
            safe.size = 4
            archive.addfile(safe, io.BytesIO(b"safe"))
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = "safe"
            archive.addfile(member)
        with pytest.raises(ValueError, match="unsafe mesh-case member"):
            runner._copy_mesh_case(blob, target)
        assert not target.exists()
    assert not (tmp_path / "absolute").exists()
    assert not (tmp_path / "escape").exists()


def test_solve_manifest_is_closed_and_omits_absent_artifacts(tmp_path):
    expected = {
        "summary.json": ("summary", "application/json"),
        "timeseries.csv": ("timeseries", "text/csv"),
        "solve-report.json": ("solve-report", "application/json"),
        "hot-internal.vtu": ("vtk", "application/octet-stream"),
        "case/logs/cht.log": ("solve-artifact", "text/plain"),
        "case/logs/foamToVTK.log": ("solve-artifact", "text/plain"),
    }
    excluded = (
        "case/constant/hot/polyMesh/points", "case/VTK/hot/case_1/internal.vtu",
        "case/logs/diagnostic.json", "case/other.log", "checkpoints/checkpoint-1.tar",
        "checkpoints.ndjson", "result.json", "output-manifest.json",
    )
    assert runner._output_manifest(tmp_path) == {"files": []}
    for name in (*expected, *excluded):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    manifest = runner._output_manifest(tmp_path)
    validate("output-manifest", manifest)
    assert {entry["path"]: (entry["kind"], entry["mediaType"]) for entry in manifest["files"]} == expected
    assert {entry["kind"] for entry in manifest["files"]} == {"summary", "timeseries", "solve-report", "vtk", "solve-artifact"}
    for entry in manifest["files"]:
        assert entry["sha256"] == sha256_file(tmp_path / entry["path"])
        assert entry["bytes"] == (tmp_path / entry["path"]).stat().st_size
    (tmp_path / "hot-internal.vtu").unlink()
    assert "vtk" not in {entry["kind"] for entry in runner._output_manifest(tmp_path)["files"]}


def test_foam_to_vtk_exports_hot_internal_and_logs_command(tmp_path, monkeypatch):
    case = tmp_path / "case"
    (case / "logs").mkdir(parents=True)

    def run(command, **kwargs):
        assert command == ["foamToVTK", "-case", str(case), "-latestTime", "-fields", "(p T)", "-allRegions", "-overwrite"]
        internal = case / "VTK/hot/case_550/internal.vtu"
        internal.parent.mkdir(parents=True)
        internal.write_bytes(b"vtk bytes")
        (case / "VTK/hot/case_metadata").write_text("not a directory")
        return subprocess.CompletedProcess(command, 0, stdout="exported")

    monkeypatch.setattr(runner.subprocess, "run", run)
    runner._export_vtk(case, tmp_path)
    assert (tmp_path / "hot-internal.vtu").read_bytes() == b"vtk bytes"
    assert (case / "logs/foamToVTK.log").read_text() == "exported"


def test_foam_to_vtk_rejects_failed_or_ambiguous_exports(tmp_path, monkeypatch):
    for scenario in ("nonzero", "absent", "multiple", "missing-internal"):
        output = tmp_path / scenario
        case = output / "case"
        (case / "logs").mkdir(parents=True)
        if scenario in ("multiple", "missing-internal"):
            (case / "VTK/hot/case_1").mkdir(parents=True)
        if scenario == "multiple":
            (case / "VTK/hot/case_2").mkdir()
        completed = subprocess.CompletedProcess([], 7 if scenario == "nonzero" else 0, stdout="diagnostic")
        monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: completed)
        with pytest.raises(RuntimeError, match="foamToVTK"):
            runner._export_vtk(case, output)
        assert not (output / "hot-internal.vtu").exists()


def _mock_solver(monkeypatch):
    def decompose(case):
        (case / "logs").mkdir()
        for region in ("hot", "cold", "solid"):
            (case / "processor0/constant" / region / "polyMesh").mkdir(parents=True)

    monkeypatch.setattr(runner, "_decompose", decompose)
    monkeypatch.setattr(runner, "_run_solver_with_checkpoints", lambda *args, **kwargs: 0)
    monkeypatch.setattr(runner, "region_cell_count", lambda *args: 1)


def test_partial_solve_exports_vtk_before_judgment_and_publishes_manifest(tmp_path, solve_document, patch_map, monkeypatch, capsys):
    work = _prepare_work(tmp_path, solve_document, patch_map)
    _mock_solver(monkeypatch)
    commands = []

    def run(command, **kwargs):
        commands.append(command[0])
        if command[0] == "foamToVTK":
            internal = work / "output/case/VTK/hot/case_10/internal.vtu"
            internal.parent.mkdir(parents=True)
            internal.write_bytes(b"partial vtk")
        return subprocess.CompletedProcess(command, 0, stdout="completed")

    def extract(case, output):
        assert commands == ["reconstructPar", "foamToVTK"]
        rows = stable_rows(10)
        path = output / "timeseries.csv"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "_extract_timeseries", extract)
    assert runner.execute(work) == 10
    output = work / "output"
    assert json.loads((output / "result.json").read_text())["outcome"] == "SUCCEEDED_WITH_WARNINGS"
    manifest = json.loads((output / "output-manifest.json").read_text())
    assert {entry["kind"] for entry in manifest["files"]} == {"summary", "timeseries", "solve-report", "vtk", "solve-artifact"}
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    phases = [event["payload"]["name"] for event in events if event["type"] == "phase"]
    assert phases == ["render", "decompose", "solve", "reconstruct", "extract", "extract"]


def test_foam_to_vtk_failure_returns_infrastructure_failed(tmp_path, solve_document, patch_map, monkeypatch):
    work = _prepare_work(tmp_path, solve_document, patch_map)
    _mock_solver(monkeypatch)
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(
        command, 7 if command[0] == "foamToVTK" else 0, stdout="tool output",
    ))
    assert runner.execute(work) == 1
    result = json.loads((work / "output/result.json").read_text())
    assert result["outcome"] == "INFRASTRUCTURE_FAILED"
    assert result["warnings"] == ["foamToVTK failed with 7"]
