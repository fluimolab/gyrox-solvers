#!/usr/bin/env python3
"""Gyrox solve `/work` entrypoint."""
from __future__ import annotations

import json
import os
import queue
import re
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from openfoam.common.io import (
    WORK_ROOT,
    find_input,
    load_request,
    manifest_entry,
    read_canonical_spec_input,
    read_json_input,
    write_json,
)
from openfoam.common.progress import ProgressWriter
from openfoam.common.schema_adapter import ContractValidationError, validate
from openfoam.solve.checkpoint import produce_checkpoint, resume_checkpoint, validate_processor_mesh
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
    if exit_code == 143:
        return "CANCELED", 143
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
    processors = sorted(path for path in case.glob("processor*") if path.is_dir())
    if not processors:
        return None
    common: set[str] | None = None
    for processor in processors:
        names = set()
        for child in processor.iterdir():
            if child.is_dir():
                try:
                    float(child.name)
                except ValueError:
                    continue
                if all((child / region).is_dir() for region in ("hot", "cold", "solid")):
                    names.add(child.name)
        common = names if common is None else common & names
    if not common:
        return None
    name = max(common, key=float)
    phys_t = float(name)
    return int(phys_t), phys_t, name


TIME_LINE = re.compile(r"^Time\s*=\s*([-+0-9.eE]+)")
REGION_LINE = re.compile(r"(?:Solving for (?:fluid|solid) region|Region\s*:?)\s+([A-Za-z0-9_.-]+)", re.I)
RESIDUAL_LINE = re.compile(
    r"(?:(hot|cold|solid)\s*[:/]\s*)?.*?Solving for\s+(Ux|Uy|Uz|p_rgh|h),\s*"
    r"Initial residual\s*=\s*([-+0-9.eE]+|nan|inf)", re.I,
)


class SolverOutputParser:
    """Translate the live OpenFOAM stream into registered progress events."""

    def __init__(self, progress: ProgressWriter, max_iters: int) -> None:
        self.progress = progress
        self.max_iters = max_iters
        self.iteration = 0
        self.region: str | None = None
        self.nonfinite = False

    def consume(self, line: str) -> None:
        if NONFINITE.search(line.encode("utf-8", errors="ignore")):
            self.nonfinite = True
        time_match = TIME_LINE.match(line.strip())
        if time_match:
            try:
                self.iteration = int(float(time_match.group(1)))
            except ValueError:
                return
            self.progress.emit("progress", {"iter": self.iteration, "maxIters": self.max_iters})
            return
        region_match = REGION_LINE.search(line)
        if region_match and region_match.group(1) in {"hot", "cold", "solid"}:
            self.region = region_match.group(1)
        residual_match = RESIDUAL_LINE.search(line)
        if residual_match and self.iteration:
            region = residual_match.group(1) or self.region
            if region not in {"hot", "cold", "solid"}:
                return
            try:
                initial = float(residual_match.group(3))
            except ValueError:
                self.nonfinite = True
                return
            if not (initial == initial and abs(initial) != float("inf")):
                self.nonfinite = True
                return
            self.progress.emit("residual", {
                "iter": self.iteration, "region": region,
                "field": residual_match.group(2), "initial": initial,
            })


def _set_stop_at(case: Path, value: str) -> None:
    control = case / "system/controlDict"
    text = control.read_text(encoding="utf-8")
    updated, count = re.subn(r"\bstopAt\s+\w+\s*;", f"stopAt          {value};", text, count=1)
    if count != 1:
        raise ValueError("controlDict stopAt entry absent")
    temporary = control.with_name(f".{control.name}.{os.getpid()}.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(control)


def _terminate_process_group(process: subprocess.Popen[str], *, grace: float) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _wait_for_graceful_write(process: subprocess.Popen[str], *, grace: float) -> bool:
    """Wait for OpenFOAM to consume writeNow; never turn SIGTERM into the write trigger."""
    try:
        return process.wait(timeout=grace) == 0
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return False


def _stream_reader(process: subprocess.Popen[str], log: Any, lines: queue.Queue[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        log.write(line)
        log.flush()
        lines.put(line)


def _run_solver_with_checkpoints(
    command: list[str], case: Path, output: Path, progress: ProgressWriter, *, interval: int,
    spec_hash: str, cores: int, max_iters: int, terminate_requested: threading.Event | None = None,
    write_grace: float = 30.0,
) -> int:
    log_path = output / "case" / "logs" / "cht.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, OMPI_ALLOW_RUN_AS_ROOT="1", OMPI_ALLOW_RUN_AS_ROOT_CONFIRM="1")
    requested = terminate_requested or threading.Event()
    parser = SolverOutputParser(progress, max_iters)
    initial_time = _latest_time(case)
    last_declared = initial_time[0] if initial_time is not None else -1
    next_checkpoint = time.monotonic() + interval
    with log_path.open("w", encoding="utf-8") as log:
        while True:
            _set_stop_at(case, "endTime")
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1, env=environment, start_new_session=True,
            )
            lines: queue.Queue[str] = queue.Queue()
            reader = threading.Thread(target=_stream_reader, args=(process, log, lines), daemon=True)
            reader.start()
            write_requested = False
            canceled = False
            graceful_write = False
            while process.poll() is None:
                try:
                    parser.consume(lines.get(timeout=0.1))
                except queue.Empty:
                    pass
                for _ in range(255):
                    try:
                        parser.consume(lines.get_nowait())
                    except queue.Empty:
                        break
                if parser.nonfinite or _has_nonfinite_output(case, log_path):
                    _terminate_process_group(process, grace=10.0)
                    reader.join(timeout=1)
                    return 20
                now = time.monotonic()
                if requested.is_set():
                    canceled = True
                    _set_stop_at(case, "writeNow")
                    graceful_write = _wait_for_graceful_write(process, grace=write_grace)
                    if not graceful_write:
                        progress.emit("log", {"level": "warn", "code": "checkpoint-io-error"})
                    break
                if now >= next_checkpoint:
                    write_requested = True
                    _set_stop_at(case, "writeNow")
                    break
            if write_requested and process.poll() is None:
                graceful_write = _wait_for_graceful_write(process, grace=write_grace)
                if not graceful_write:
                    progress.emit("log", {"level": "warn", "code": "checkpoint-io-error"})
            reader.join(timeout=1)
            while not lines.empty():
                parser.consume(lines.get_nowait())
            if parser.nonfinite:
                return 20

            if write_requested or canceled:
                if not graceful_write:
                    return 1 if canceled else int(process.returncode or 1)
                checkpoint_created = False
                latest = _latest_time(case)
                if latest is not None and latest[0] > last_declared:
                    iteration, phys_t, name = latest
                    try:
                        declaration = produce_checkpoint(
                            case, output, spec_hash=spec_hash, decomp_n=cores,
                            iteration=iteration, phys_t=phys_t, time_name=name,
                        )
                        progress.emit("checkpoint", declaration)
                        last_declared = iteration
                        checkpoint_created = True
                    except (OSError, ValueError):
                        progress.emit("log", {"level": "warn", "code": "checkpoint-io-error"})
                        if canceled:
                            return 1
                elif latest is not None:
                    progress.emit("log", {"level": "warn", "code": "checkpoint-io-error"})
                if canceled:
                    return 143 if checkpoint_created else 1
                if not checkpoint_created:
                    return 1
                next_checkpoint = time.monotonic() + interval
                if parser.iteration < max_iters and process.returncode == 0:
                    continue
            return int(process.returncode or 0)


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


def _nonfinite_judgment(report_values: dict[str, Any] | None = None) -> JudgeResult:
    qoi_axis = {
        "drift": {"pass": False, "driftPct": 0.0},
        "span": {"pass": False, "spanPct": 0.0},
        "instSpanPct": 0.0,
        "amplitudePct": 0.0,
    }
    axes = {
        "perQoI": {key: dict(qoi_axis) for key in ("dpHotPa", "dpColdPa", "qW")},
        "tOutHot": {"driftK": 0.0, "spanK": 0.0, "pass": False},
        "tOutCold": {"driftK": 0.0, "spanK": 0.0, "pass": False},
        "eb": {"windowMeanPct": 0.0, "flag": "ok", "pass": False},
        "mass": {"hotPct": 0.0, "coldPct": 0.0, "pass": False},
        "sign": {"pass": False},
        "finite": {"pass": False},
        "insufficient": True,
    }
    return JudgeResult(20, "PHYSICS_DIVERGED", False, False, False, axes, report_values or {"iterations": None})


def _best_effort_divergence_summary(
    case: Path, output: Path, solve_document: dict[str, Any], *, started: float, warnings: list[str],
) -> dict[str, Any]:
    judgment = _nonfinite_judgment()
    try:
        timeseries = _extract_timeseries(case, output)
        diagnostic = judge_csv(timeseries, solve_document["payload"]["convergence"])
        judgment = _nonfinite_judgment(diagnostic.report_values)
    except Exception as exc:
        warnings.append(f"non-finite diagnostic extraction: {type(exc).__name__}: {exc}")
    cells: int | None = None
    try:
        cells = sum(region_cell_count(case, region) for region in ("hot", "cold", "solid"))
    except Exception as exc:
        warnings.append(f"non-finite cell count: {type(exc).__name__}: {exc}")
    summary = _summary(judgment, wall_clock=time.monotonic() - started, cells=cells)
    write_json(output / "summary.json", summary)
    return summary


def _decompose(case: Path) -> None:
    completed = subprocess.run(
        ["decomposePar", "-case", str(case), "-allRegions", "-force"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
    )
    (case / "logs").mkdir(exist_ok=True)
    (case / "logs/decomposePar.log").write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"decomposePar failed with {completed.returncode}")


def execute(work_root: Path = WORK_ROOT, *, terminate_requested: threading.Event | None = None) -> int:
    started = time.monotonic()
    progress = ProgressWriter()
    output = work_root / "output"
    output.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    outcome, exit_code = "INFRASTRUCTURE_FAILED", 1
    summary: dict[str, Any] | None = None
    nonfinite_outcome_locked = False
    try:
        request = load_request(work_root, "solve")
        solve_document = read_canonical_spec_input(
            request, work_root, "solve", ("solve-spec", "solve-spec.json"),
        )
        patch_map = json.loads(find_input(request, work_root, ("patch-map", "patch-map.json")).read_text(encoding="utf-8"))
        run_manifest = read_json_input(request, work_root, ("run-manifest", "run-manifest.json"))
        validate("patch-map", patch_map)
        validate("run-manifest", run_manifest)
        executor_policy(request, run_manifest)
        checkpoint_interval = request["limits"]["checkpointIntervalSec"]
        cores = request["limits"]["cpuCores"]
        spec_hash = request["specs"]["solve"]["hash"]

        case = output / "case"
        mesh_case = find_input(request, work_root, ("mesh-case", "mesh"))
        _copy_mesh_case(mesh_case, case)
        progress.emit("phase", {"name": "render"})
        rendered = render_case(solve_document, patch_map, case, cores=cores, write_interval=500)
        write_json(output / "solve-report.json", rendered.report)

        progress.emit("phase", {"name": "decompose"})
        _decompose(case)
        validate_processor_mesh(case, cores)

        checkpoint_items = [item for item in request["inputs"] if item["kind"] == "checkpoint"]
        if checkpoint_items:
            archive = find_input(request, work_root, ("checkpoint",))
            sidecar = archive.with_name("checkpoint.meta.json")
            decision = resume_checkpoint(case, archive, sidecar, spec_hash=spec_hash, decomp_n=cores)
            if decision.exit_code == 40:
                outcome, exit_code = "INVALID_INPUT", 40
                raise RuntimeError("__checkpoint_rejected__")

        progress.emit("phase", {"name": "solve"})
        solver_code = _run_solver_with_checkpoints(
            ["mpirun", "-np", str(cores), "chtMultiRegionSimpleFoam", "-parallel", "-case", str(case)],
            case, output, progress, interval=checkpoint_interval, spec_hash=spec_hash, cores=cores,
            max_iters=int(solve_document["payload"]["limits"]["maxIters"]),
            terminate_requested=terminate_requested,
        )
        if solver_code == 20:
            progress.emit("phase", {"name": "extract"})
            outcome, exit_code = "PHYSICS_DIVERGED", 20
            nonfinite_outcome_locked = True
            summary = _best_effort_divergence_summary(
                case, output, solve_document, started=started, warnings=warnings,
            )
            raise RuntimeError("__solver_failed__")
        if solver_code == 143:
            outcome, exit_code = map_process_exit(solver_code)
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
        if not nonfinite_outcome_locked:
            outcome, exit_code = "INVALID_INPUT", 30
    except Exception as exc:
        warnings.append(f"{type(exc).__name__}: {exc}")
        if not nonfinite_outcome_locked:
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
    terminate_requested = threading.Event()

    def handle_sigterm(_signum: int, _frame: Any) -> None:
        terminate_requested.set()

    signal.signal(signal.SIGTERM, handle_sigterm)
    return execute(terminate_requested=terminate_requested)


if __name__ == "__main__":
    sys.exit(main())
