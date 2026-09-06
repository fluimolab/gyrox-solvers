from __future__ import annotations

import json
import os
import stat
import subprocess
import tarfile

import numpy as np

from openfoam.common.io import sha256_file
from openfoam.common.schema_adapter import canonical_hash, validate
import openfoam.mesh.convert as converter
from openfoam.mesh.qa import run_check_mesh
from openfoam.mesh.vti_io import write_vti_uint8


def test_mesh_case_tar_is_deterministic_and_preserves_modes(tmp_path):
    case = tmp_path / "case"
    for name in ("z/owner", "a/points", "a/" + "long-name" * 20):
        path = case / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        path.chmod(0o640)
    (case / "empty").mkdir(mode=0o750)
    first, second = tmp_path / "first.tar", tmp_path / "second.tar"
    converter._package_mesh_case(case, first)
    for path in case.rglob("*"):
        os.utime(path, (1234567890, 1234567890))
    converter._package_mesh_case(case, second)
    assert sha256_file(first) == sha256_file(second)
    with tarfile.open(first) as archive:
        assert archive.getnames() == sorted(path.relative_to(case).as_posix() for path in case.rglob("*"))
        for member in archive.getmembers():
            assert (member.uid, member.gid, member.uname, member.gname, member.mtime) == (0, 0, "", "", 0)
            assert member.mode == stat.S_IMODE((case / member.name).stat().st_mode)


def _mesh_work(repo_root, tmp_path, monkeypatch, *, passed):
    baseline = json.loads((repo_root / "contract/fixtures/hash/accept/A1-four-spec-baseline.json").read_text())
    inputs = tmp_path / "input"
    inputs.mkdir()
    specs, declared = {}, []
    for slot in ("geometry", "discretization"):
        kind = f"{slot}-spec"
        document = baseline["inputs"][slot]
        digest = canonical_hash(kind, document)[0]
        name = f"{slot}.json"
        (inputs / name).write_text(json.dumps(document))
        specs[slot] = {"kind": kind, "hash": digest}
        declared.append({"kind": kind, "hash": digest, "path": name})
    write_vti_uint8(str(inputs / "labels.vti"), np.array([[[1, 1], [3, 3], [2, 2]]], dtype=np.uint8), 0.1)
    declared.append({"kind": "labels", "hash": "a" * 64, "path": "labels.vti"})
    (tmp_path / "request.json").write_text(json.dumps({
        "contractVersion": 1, "stage": "mesh", "specs": specs, "inputs": declared,
        "limits": {"cpuCores": 1, "memBytes": 1024},
    }))
    patch_bytes = []
    original = converter.labels_to_foam.convert

    def convert(*args, **kwargs):
        report = original(*args, **kwargs)
        patch_bytes.append((tmp_path / "scratch/case/patch-map.json").read_bytes())
        return report

    completed = subprocess.CompletedProcess([], 0 if passed else 1, stdout="Mesh OK." if passed else "Failed 1 mesh checks")
    monkeypatch.setattr(converter.labels_to_foam, "convert", convert)
    monkeypatch.setattr(converter, "run_check_mesh", lambda case, qa: run_check_mesh(
        case, qa, run=lambda *args, **kwargs: completed,
    ))
    return patch_bytes


def test_mesh_manifest_declares_stage_artifacts_without_changing_patch_map(repo_root, tmp_path, monkeypatch):
    patch_bytes = _mesh_work(repo_root, tmp_path, monkeypatch, passed=True)
    assert converter.execute(tmp_path) == 0
    output = tmp_path / "output"
    manifest = json.loads((output / "output-manifest.json").read_text())
    validate("output-manifest", manifest)
    assert {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()} == {
        entry["path"] for entry in manifest["files"]
    } | {"output-manifest.json", "result.json"}
    assert {entry["path"]: (entry["kind"], entry["mediaType"]) for entry in manifest["files"]} == {
        "mesh-case.tar": ("mesh-case", "application/x-tar"),
        "patch-map.json": ("patch-map", "application/json"),
        "convert-report.json": ("convert-report", "application/json"),
        "mesh-qa.json": ("mesh-artifact", "application/json"),
        "checkMesh.log": ("mesh-artifact", "text/plain"),
    }
    assert (output / "patch-map.json").read_bytes() == patch_bytes[0]
    with tarfile.open(output / "mesh-case.tar") as archive:
        assert archive.extractfile("patch-map.json").read() == patch_bytes[0]
        assert set(archive.getnames()) == {path.relative_to(tmp_path / "scratch/case").as_posix() for path in (tmp_path / "scratch/case").rglob("*")}
    for entry in manifest["files"]:
        assert entry["sha256"] == sha256_file(output / entry["path"])


def test_failed_mesh_qa_publishes_diagnostics_without_mesh_case(repo_root, tmp_path, monkeypatch):
    _mesh_work(repo_root, tmp_path, monkeypatch, passed=False)
    assert converter.execute(tmp_path) == 20
    output = tmp_path / "output"
    assert not (output / "mesh-case.tar").exists()
    manifest = json.loads((output / "output-manifest.json").read_text())
    assert len(manifest["files"]) == 4
    assert all(entry["kind"] != "mesh-case" for entry in manifest["files"])
