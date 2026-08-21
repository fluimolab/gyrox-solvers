#!/usr/bin/env python3
"""Gyrox solve `/work` entrypoint."""
from __future__ import annotations

import json
import os
import re
import resource
import shutil
import subprocess
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
from openfoam.solve.checkpoint import produce_checkpoint, resume_checkpoint
from openfoam.solve.extract import build_timeseries
from openfoam.solve.extract.common import region_cell_count
from openfoam.solve.judge import JudgeResult, judge_csv
from openfoam.solve.policy import executor_policy
from openfoam.solve.renderer import render_case


SUMMARY_VALUE_KEYS = (
    "dpHotPa", "dpColdPa", "mdotHotKgS", "mdotColdKgS", "fFactor", "qW", "qHotW", "qColdW",
    "effectiveness", "ntu", "uaWK", "jFactor", "tOutHotK", "tOutColdK", "energyBalancePct",
)


def verification_defaults() -> dict[str, Any]:
    return {
        "dpHot": {"status": "regression_only"},
        "dpCold": {"status": "judgment_not_established", "ref": "D-M06-1"},
        "thermal": {"status": "unverified_underprediction", "noticeId": "D8-2", "dm051": True},
        "holds": None,
    }


def map_process_exit(exit_code: int, *, oom_killed: bool = False) -> tuple[str, int]:
    if exit_code == 0:
        return "SUCCEEDED", 0
    if exit_code == 10:
        return "SUCCEEDED_WITH_WARNINGS", 10
    if exit_code == 20:
        return "PHYSICS_DIVERGED", 20
    if exit_code in (30, 40):
        return "INVALID_INPUT", exit_code
    if exit_code == 137 and oom_killed:
        return "RESOURCE_EXHAUSTED", 137
    return "INFRASTRUCTURE_FAILED", exit_code


def _oom_killed() -> bool:
    path = Path("/sys/fs/cgroup/memory.events")
    if not path.is_file():
        return False
    values = dict(line.split() for line in path.read_text().splitlines() if len(line.split()) == 2)
    return int(values.get("oom_kill", "0")) > 0


def _copy_mesh_case(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise ValueError("mesh-case input must be a directory")
    shutil.copytree(source, target, dirs_exist_ok=True)


def _latest_time(case: Path) -> tuple[int, float, str] | None:
    candidates: list[tuple[float, str]] = []
    for processor in case.glob("processor*"):
        for child in processor.iterdir() if processor.is_dir() else ():
            if child.is_dir():
                try:
                    candidates.append((float(child.name), child.name))
                except ValueError:
                    pass
    if not candidates:
        return None
    phys_t, name = max(candidates)
    return int(phys_t), phys_t, name


def _run_solver_with_checkpoints(
    command: list[str], case: Path, output: Path, progress: ProgressWriter, *, interval: int, spec_hash: str, cores: int,
) -> int:
    log_path = output / "case" / "logs" / "cht.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, OMPI_ALLOW_RUN_AS_ROOT="1", OMPI_ALLOW_RUN_AS_ROOT_CONFIRM="1")
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, text=True, env=environment)
        next_checkpoint = time.monotonic() + interval
        next_finite_check = time.monotonic() + 1.0
        while process.poll() is None:
            now = time.monotonic()
            if now >= next_finite_check:
                if _has_nonfinite_output(case, log_path):
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    return 20
                next_finite_check = now + 1.0
            if now >= next_checkpoint:
                latest = _latest_time(case)
                if latest is not None:
                    iteration, phys_t, name = latest
                    try:
                        declaration = produce_checkpoint(
                            case, output, spec_hash=spec_hash, decomp_n=cores,
                            iteration=iteration, phys_t=phys_t, time_name=name,
                        )
                        progress.emit("checkpoint", declaration)
                    except (OSError, ValueError):
                        progress.emit("log", {"level": "warn", "code": "checkpoint-io-error"})
                next_checkpoint += interval
            time.sleep(0.25)
        return int(process.returncode)


NONFINITE = re.compile(rb"(?<![A-Za-z])(?:nan|[-+]?inf)(?![A-Za-z])", re.I)


def _has_nonfinite_output(case: Path, solver_log: Path) -> bool:
    paths = [solver_log, *case.glob("postProcessing/**/*.dat")]
    for path in paths:
        try:
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 8192))
                if NONFINITE.search(stream.read()):
                    return True
        except OSError:
            continue
    return False


def _extract_timeseries(case: Path, output: Path) -> Path:
    out_csv = output / "timeseries.csv"
    config = {
        "roles": {"hot": {"region": "hot"}, "cold": {"region": "cold"}},
        "residualFields": {"fluid": ["U", "p_rgh", "h"], "solid": ["h"]},
        "solidRegions": ["solid"],
    }
    build_timeseries(case, config, out_csv)
    return out_csv


def _summary(judgment: JudgeResult, *, wall_clock: float, cells: int | None) -> dict[str, Any]:
    summary = {
        key: None if judgment.exit_code == 20 else judgment.report_values.get(key)
        for key in SUMMARY_VALUE_KEYS
    }
    summary.update({
        "converged": judgment.converged,
        "convergenceAxes": judgment.axes,
        "convergenceNotice": {"noticeId": "nonconverged-partial"} if judgment.exit_code == 10 else None,
        "iterations": judgment.report_values.get("iterations"),
        "wallClockS": wall_clock,
        "cells": cells,
        "windowMeanConverged": judgment.window_mean_converged,
        "dpHotWindowMeanConverged": judgment.dp_hot_window_mean_converged,
        "verification": verification_defaults(),
    })
    validate("summary", summary)
    return summary


def execute(work_root: Path = WORK_ROOT) -> int:
    started = time.monotonic()
    progress = ProgressWriter()
    output = work_root / "output"
    output.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    outcome, exit_code = "INFRASTRUCTURE_FAILED", 1
    summary: dict[str, Any] | None = None
    try:
        request = load_request(work_root, "solve")
        solve_document = read_json_input(request, work_root, ("solve-spec", "solve-spec.json"))
        patch_map = json.loads(find_input(request, work_root, ("patch-map", "patch-map.json")).read_text(encoding="utf-8"))
        run_manifest = read_json_input(request, work_root, ("run-manifest", "run-manifest.json"))
        validate("solve-spec", solve_document)
        validate("patch-map", patch_map)
        validate("run-manifest", run_manifest)
        policy = executor_policy(request, run_manifest)
        checkpoint_interval = request["limits"]["checkpointIntervalSec"]
        cores = request["limits"]["cpuCores"]
        spec_hash = request["specs"]["solve"]["hash"]

        case = output / "case"
        mesh_case = find_input(request, work_root, ("mesh-case", "mesh"))
        _copy_mesh_case(mesh_case, case)
        progress.emit("phase", {"name": "render"})
        rendered = render_case(solve_document, patch_map, case, cores=cores, write_interval=500)
        write_json(output / "solve-report.json", rendered.report)

        resumed = False
        checkpoint_items = [item for item in request["inputs"] if item["kind"] == "checkpoint"]
        if checkpoint_items:
            archive = find_input(request, work_root, ("checkpoint",))
            sidecar = archive.with_name("checkpoint.meta.json")
            decision = resume_checkpoint(case, archive, sidecar, spec_hash=spec_hash, decomp_n=cores)
            if decision.exit_code == 40:
                outcome, exit_code = "INVALID_INPUT", 40
                raise RuntimeError("__checkpoint_rejected__")
            resumed = decision.action == "resume"

        if not resumed:
            progress.emit("phase", {"name": "decompose"})
            completed = subprocess.run(
                ["decomposePar", "-case", str(case), "-allRegions", "-force"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
            )
            (case / "logs").mkdir(exist_ok=True)
            (case / "logs/decomposePar.log").write_text(completed.stdout or "", encoding="utf-8")
            if completed.returncode != 0:
                raise RuntimeError(f"decomposePar failed with {completed.returncode}")

        progress.emit("phase", {"name": "solve"})
        solver_code = _run_solver_with_checkpoints(
            ["mpirun", "-np", str(cores), "chtMultiRegionSimpleFoam", "-parallel", "-case", str(case)],
            case, output, progress, interval=checkpoint_interval, spec_hash=spec_hash, cores=cores,
        )
        if solver_code == 20:
            progress.emit("phase", {"name": "extract"})
            timeseries = _extract_timeseries(case, output)
            judgment = judge_csv(timeseries, solve_document["payload"]["convergence"])
            axes = dict(judgment.axes)
            axes["finite"] = {"pass": False}
            judgment = JudgeResult(20, "PHYSICS_DIVERGED", False, False, False, axes, judgment.report_values)
            cells = sum(region_cell_count(case, region) for region in ("hot", "cold", "solid"))
            summary = _summary(judgment, wall_clock=time.monotonic() - started, cells=cells)
            write_json(output / "summary.json", summary)
            outcome, exit_code = "PHYSICS_DIVERGED", 20
            raise RuntimeError("__solver_failed__")
        if solver_code != 0:
            outcome, exit_code = map_process_exit(solver_code, oom_killed=_oom_killed())
            raise RuntimeError("__solver_failed__")

        progress.emit("phase", {"name": "reconstruct"})
        completed = subprocess.run(
            ["reconstructPar", "-case", str(case), "-allRegions", "-latestTime"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
        )
        (case / "logs/reconstructPar.log").write_text(completed.stdout or "", encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(f"reconstructPar failed with {completed.returncode}")

        progress.emit("phase", {"name": "extract"})
        timeseries = _extract_timeseries(case, output)
        judgment = judge_csv(timeseries, solve_document["payload"]["convergence"])
        cells = sum(region_cell_count(case, region) for region in ("hot", "cold", "solid"))
        summary = _summary(judgment, wall_clock=time.monotonic() - started, cells=cells)
        write_json(output / "summary.json", summary)
        outcome, exit_code = judgment.outcome, judgment.exit_code
    except RuntimeError as exc:
        if str(exc) not in {"__checkpoint_rejected__", "__solver_failed__"}:
            warnings.append(str(exc))
    except (ValueError, KeyError, FileNotFoundError, ContractValidationError) as exc:
        warnings.append(str(exc))
        outcome, exit_code = "INVALID_INPUT", 30
    except Exception as exc:
        warnings.append(f"{type(exc).__name__}: {exc}")
        outcome, exit_code = "INFRASTRUCTURE_FAILED", 1

    if summary is not None:
        progress.emit(
            "phase", {"name": "extract"}, outcome=outcome,
            converged=summary["converged"],
            windowMeanConverged=summary["windowMeanConverged"],
            dpHotWindowMeanConverged=summary["dpHotWindowMeanConverged"],
        )
    else:
        progress.emit("phase", {"name": "extract"}, outcome=outcome)

    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name not in {"output-manifest.json", "result.json"}:
            media = "application/json" if path.suffix == ".json" else "text/csv" if path.suffix == ".csv" else "application/octet-stream"
            kind = "summary" if path.name == "summary.json" else "solve-artifact"
            files.append(manifest_entry(path, output, kind, media))
    manifest = {"files": files}
    validate("output-manifest", manifest)
    write_json(output / "output-manifest.json", manifest)
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
    return exit_code


def main() -> int:
    return execute()


if __name__ == "__main__":
    sys.exit(main())
