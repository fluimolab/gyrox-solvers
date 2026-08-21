from __future__ import annotations

import copy
import json

import pytest

from openfoam.common.schema_adapter import canonical_hash
from openfoam.mesh.convert import execute, validate_converter_topology


def test_x_topology_is_accepted_and_non_x_fails_closed(repo_root, tmp_path):
    baseline = json.loads((repo_root / "contract/fixtures/hash/accept/A1-four-spec-baseline.json").read_text())
    geometry = copy.deepcopy(baseline["inputs"]["geometry"]["payload"])
    validate_converter_topology(geometry)
    geometry["ports"][0]["face"] = "+y"
    with pytest.raises(ValueError, match="counterflow-x only"):
        validate_converter_topology(geometry)


def test_non_x_work_request_returns_exit_30_with_explicit_message(repo_root, tmp_path):
    baseline = json.loads((repo_root / "contract/fixtures/hash/accept/A1-four-spec-baseline.json").read_text())
    geometry = copy.deepcopy(baseline["inputs"]["geometry"])
    geometry["payload"]["ports"][0]["face"] = "+y"
    discretization = baseline["inputs"]["discretization"]
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "geometry.json").write_text(json.dumps(geometry))
    (input_root / "discretization.json").write_text(json.dumps(discretization))
    geometry_hash = canonical_hash("geometry-spec", geometry)[0]
    discretization_hash = canonical_hash("discretization-spec", discretization)[0]
    request = {
        "contractVersion": 1,
        "stage": "mesh",
        "specs": {
            "geometry": {"kind": "geometry-spec", "hash": geometry_hash},
            "discretization": {"kind": "discretization-spec", "hash": discretization_hash},
        },
        "inputs": [
            {"kind": "geometry-spec", "hash": "a" * 64, "path": "geometry.json"},
            {"kind": "discretization-spec", "hash": "b" * 64, "path": "discretization.json"},
            {"kind": "labels", "hash": "c" * 64, "path": "unused-labels.vti"},
        ],
        "limits": {"cpuCores": 1, "memBytes": 1024},
    }
    (tmp_path / "request.json").write_text(json.dumps(request))
    assert execute(tmp_path) == 30
    result = json.loads((tmp_path / "output/result.json").read_text())
    assert result["outcome"] == "INVALID_INPUT"
    assert "counterflow-x only" in result["warnings"][0]


def test_mesh_work_request_rejects_canonical_geometry_hash_mismatch(repo_root, tmp_path):
    baseline = json.loads((repo_root / "contract/fixtures/hash/accept/A1-four-spec-baseline.json").read_text())
    geometry = copy.deepcopy(baseline["inputs"]["geometry"])
    discretization = baseline["inputs"]["discretization"]
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "geometry.json").write_text(json.dumps(geometry))
    (input_root / "discretization.json").write_text(json.dumps(discretization))
    request = {
        "contractVersion": 1, "stage": "mesh",
        "specs": {
            "geometry": {"kind": "geometry-spec", "hash": "9" * 64},
            "discretization": {
                "kind": "discretization-spec",
                "hash": canonical_hash("discretization-spec", discretization)[0],
            },
        },
        "inputs": [
            {"kind": "geometry-spec", "hash": "a" * 64, "path": "geometry.json"},
            {"kind": "discretization-spec", "hash": "b" * 64, "path": "discretization.json"},
            {"kind": "labels", "hash": "c" * 64, "path": "unused-labels.vti"},
        ],
        "limits": {"cpuCores": 1, "memBytes": 1024},
    }
    (tmp_path / "request.json").write_text(json.dumps(request))
    assert execute(tmp_path) == 30
    result = json.loads((tmp_path / "output/result.json").read_text())
    assert "canonical spec hash mismatch for geometry" in result["warnings"][0]
