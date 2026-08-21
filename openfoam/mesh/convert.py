#!/usr/bin/env python3
"""Gyrox mesh `/work` entrypoint."""
from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path
from typing import Any

from openfoam.common.io import (
    WORK_ROOT,
    find_input,
    load_request,
    manifest_entry,
    read_json_input,
    write_json,
)
from openfoam.common.progress import ProgressWriter
from openfoam.common.schema_adapter import ContractValidationError, validate
from openfoam.mesh import labels_to_foam
from openfoam.mesh.qa import run_check_mesh


DIAGNOSTIC_KEYS = (
    "input", "case", "format", "dims", "spacing_mm", "origin_mm",
    "label_histogram", "cyclic_axes", "cyclic_check", "regions",
    "cell_count_check", "artifact_sha256", "wall_time_s", "peak_rss_mb",
)
PORT_PATCHES = {
    "port.hot.inlet": ("hot", "hot_inlet"),
    "port.hot.outlet": ("hot", "hot_outlet"),
    "port.cold.inlet": ("cold", "cold_inlet"),
    "port.cold.outlet": ("cold", "cold_outlet"),
}


def product_report(legacy: dict[str, Any]) -> dict[str, Any]:
    sy, sz = legacy["spacing_mm"][1:]
    face_area_m2 = float(sy) * float(sz) * 1e-6
    areas = {}
    for geom_ref, (region, patch) in PORT_PATCHES.items():
        count = legacy["regions"][region]["patch_counts"].get(patch, 0)
        areas[geom_ref] = count * face_area_m2
    report = {
        "schemaVersion": 1,
        "meshCombinedSha": legacy["artifact_sha256"]["combined"],
        "portAreasM2": areas,
        "diagnostics": {key: legacy[key] for key in DIAGNOSTIC_KEYS},
    }
    validate("convert-report", report)
    return report


def validate_port_area_bounds(report: dict[str, Any], geometry: dict[str, Any]) -> None:
    sizes = geometry["envelope"]["sizeM"]
    ports = {f"port.{port['id'].replace('-', '.')}": port for port in geometry["ports"]}
    axis_index = {"x": 0, "y": 1, "z": 2}
    for geom_ref, value in report["portAreasM2"].items():
        port = ports[geom_ref]
        normal = axis_index[port["face"][-1]]
        upper = sizes[(normal + 1) % 3] * sizes[(normal + 2) % 3]
        if not 0 < value <= upper:
            raise ValueError(f"port area outside envelope face bound: {geom_ref}")


def _documents(request: dict[str, Any], work_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    geometry = read_json_input(request, work_root, ("geometry-spec", "geometry-spec.json"))
    discretization = read_json_input(request, work_root, ("discretization-spec", "discretization-spec.json"))
    validate("geometry-spec", geometry)
    validate("discretization-spec", discretization)
    return geometry["payload"], discretization["payload"]


def execute(work_root: Path = WORK_ROOT) -> int:
    started = time.monotonic()
    progress = ProgressWriter()
    output = work_root / "output"
    output.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    try:
        request = load_request(work_root, "mesh")
        geometry, discretization = _documents(request, work_root)
        labels = find_input(request, work_root, ("labels", "labels-vti", "volume"), suffix="labels.vti")
        progress.emit("phase", {"name": "render"})
        legacy = labels_to_foam.convert(str(labels), str(output / "case"), fmt="binary")
        report = product_report(legacy)
        validate_port_area_bounds(report, geometry)
        report_path = output / "case" / "convert-report.json"
        write_json(report_path, report)

        progress.emit("phase", {"name": "extract"})
        qa = run_check_mesh(output / "case", discretization["qa"])
        qa_log = output / "case" / "checkMesh.log"
        qa_log.write_text(qa.pop("log"), encoding="utf-8")
        qa_path = output / "case" / "mesh-qa.json"
        write_json(qa_path, qa)
        outcome, exit_code = ("SUCCEEDED", 0) if qa["passed"] else ("MESH_QA_FAILED", 20)

        files = []
        for path in sorted((output / "case").rglob("*")):
            if path.is_file() and "polyMesh/sets/" not in path.as_posix():
                media = "application/json" if path.suffix == ".json" else "text/plain"
                if path.name in {"points", "faces", "owner", "neighbour"}:
                    media = "application/octet-stream"
                files.append(manifest_entry(path, output, "mesh-artifact", media))
        manifest = {"files": files}
        validate("output-manifest", manifest)
        write_json(output / "output-manifest.json", manifest)
    except (ValueError, FileNotFoundError, ContractValidationError) as exc:
        progress.emit("log", {"level": "error", "code": "render-warning"})
        warnings.append(str(exc))
        outcome, exit_code = "INVALID_INPUT", 30
    except Exception as exc:  # external tool or converter failure is infrastructure unless QA classified it
        progress.emit("log", {"level": "error", "code": "solver-warning"})
        warnings.append(f"{type(exc).__name__}: {exc}")
        outcome, exit_code = "INFRASTRUCTURE_FAILED", 1

    result = {
        "outcome": outcome,
        "exitCode": exit_code,
        "metrics": {
            "wallClockS": time.monotonic() - started,
            "peakRssBytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        },
        "warnings": warnings,
    }
    validate("work-result", result)
    write_json(output / "result.json", result)
    progress.emit("phase", {"name": "extract"}, outcome=outcome)
    return exit_code


def main() -> int:
    return execute()


if __name__ == "__main__":
    sys.exit(main())
