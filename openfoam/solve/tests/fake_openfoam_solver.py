#!/usr/bin/env python3
"""Small process-compatible OpenFOAM stand-in for runner integration tests."""
from __future__ import annotations

import os
import re
import signal
import sys
import time
from pathlib import Path


case = Path(sys.argv[1])
mode = os.environ.get("FAKE_OPENFOAM_MODE", "complete")
current = 0
terminate = False


def latest() -> int:
    values = []
    for child in (case / "processor0").iterdir():
        if child.is_dir():
            try:
                values.append(int(float(child.name)))
            except ValueError:
                pass
    return max(values, default=0)


def write_time(iteration: int) -> None:
    for processor in sorted(case.glob("processor*")):
        for region in ("hot", "cold", "solid"):
            target = processor / str(iteration) / region
            target.mkdir(parents=True, exist_ok=True)
            (target / "T").write_text(f"iteration {iteration}\n")


def handler(_signum, _frame) -> None:
    global terminate
    terminate = True


def write_dat(name: str, rows: list[tuple[float, ...]], header: str = "Time value") -> None:
    target = case / "postProcessing" / name / "0" / (
        "solverInfo.dat" if name.startswith("solverInfo") else
        "wallHeatFlux.dat" if name.startswith("whf_") else "surfaceFieldValue.dat"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# " + header + "\n" + "\n".join(" ".join(map(str, row)) for row in rows) + "\n")


def write_post_processing(start: int, end: int) -> None:
    iterations = range(start, end + 1)
    fluid = [(i, 1e-3, 1e-3, 1e-3, 1e-3, 1e-3) for i in iterations]
    solid = [(i, 1e-3) for i in range(start, end + 1)]
    for role in ("hot", "cold"):
        write_dat(f"solverInfo_{role}", fluid, "Time Ux_initial Uy_initial Uz_initial p_rgh_initial h_initial")
    write_dat("solverInfo_solid_solid", solid, "Time h_initial")
    values = {
        "mdot_hot_in": -0.02, "mdot_hot_out": 0.02,
        "mdot_cold_in": -0.02, "mdot_cold_out": 0.02,
        "p_hot_in": 200.0, "p_hot_out": 0.0,
        "p_cold_in": 250.0, "p_cold_out": 0.0,
        "T_hot_in": 333.0, "T_hot_out": 315.0,
        "T_cold_in": 293.0, "T_cold_out": 310.0,
    }
    for name, value in values.items():
        write_dat(name, [(i, value) for i in range(start, end + 1)])
    write_dat("whf_hot", [(i, 0.0, 0.0, 0.0, -1500.0) for i in range(start, end + 1)], "Time min max average integral")
    write_dat("whf_cold", [(i, 0.0, 0.0, 0.0, 1500.0) for i in range(start, end + 1)], "Time min max average integral")


signal.signal(signal.SIGTERM, handler)
start = latest() + 1
end = 550 if mode == "complete" else 100000
for current in range(start, end + 1):
    print(f"Time = {current}", flush=True)
    print("Solving for fluid region hot", flush=True)
    print("smoothSolver: Solving for p_rgh, Initial residual = 0.001, Final residual = 0.0001, No Iterations 1", flush=True)
    if terminate or "stopAt          writeNow;" in (case / "system/controlDict").read_text():
        write_time(current)
        sys.exit(0)
    if mode != "complete":
        time.sleep(0.01)

write_time(end)
write_post_processing(start, end)
