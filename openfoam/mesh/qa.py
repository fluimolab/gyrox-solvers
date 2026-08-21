"""checkMesh invocation and fail-closed quality verdict."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


VIOLATION_PATTERNS = (
    re.compile(r"Failed\s+(\d+)\s+mesh checks?", re.I),
    re.compile(r"(\d+)\s+(?:faces|cells|edges)\s+failed", re.I),
    re.compile(r"(?:violation|error)s?\s*[:=]\s*(\d+)", re.I),
)


def violation_count(text: str, return_code: int) -> int:
    counts = [int(match.group(1)) for pattern in VIOLATION_PATTERNS for match in pattern.finditer(text)]
    explicit_failure = "Failed" in text or "***Error" in text or "FATAL" in text
    if counts:
        return sum(counts)
    if return_code != 0 or explicit_failure:
        return 1
    return 0


def write_mesh_quality_dict(case: Path, qa: dict[str, Any]) -> None:
    system = case / "system"
    system.mkdir(parents=True, exist_ok=True)
    values = "\n".join(f"    {key:<28} {value};" for key, value in qa.items())
    (system / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application checkMesh;\n"
        "startFrom startTime;\n"
        "startTime 0;\n"
        "stopAt endTime;\n"
        "endTime 1;\n"
        "deltaT 1;\n"
        "writeControl timeStep;\n"
        "writeInterval 1;\n"
        "purgeWrite 0;\n"
        "writeFormat ascii;\n"
        "writePrecision 10;\n"
        "writeCompression off;\n"
        "timeFormat general;\n"
        "timePrecision 6;\n"
        "runTimeModifiable false;\n", encoding="utf-8")
    fv_schemes = (
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default none; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n"
        "wallDist { method meshWave; }\n"
    )
    fv_solution = (
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers {}\n"
    )
    region_names = sorted(
        entry.name
        for entry in (case / "constant").iterdir()
        if entry.is_dir() and (entry / "polyMesh").is_dir()
    )
    if not region_names:
        raise ValueError("no region polyMesh directories available for checkMesh")
    for region in region_names:
        region_system = system / region
        region_system.mkdir(parents=True, exist_ok=True)
        (region_system / "meshQualityDict").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object meshQualityDict; }\n"
            + values + "\n", encoding="utf-8")
        (region_system / "fvSchemes").write_text(fv_schemes, encoding="utf-8")
        (region_system / "fvSolution").write_text(fv_solution, encoding="utf-8")


def run_check_mesh(case: Path, qa: dict[str, Any], run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> dict[str, Any]:
    write_mesh_quality_dict(case, qa)
    completed = run(
        ["checkMesh", "-case", str(case), "-allRegions", "-meshQuality"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log = completed.stdout or ""
    # checkMesh writes disconnected-region diagnostics into otherwise canonical
    # mesh directories. They are QA scratch data, not converter products.
    for region in (case / "constant").iterdir():
        if not region.is_dir():
            continue
        (region / "cellToRegion").unlink(missing_ok=True)
        sets = region / "polyMesh/sets"
        if sets.is_dir():
            shutil.rmtree(sets)
    count = violation_count(log, completed.returncode)
    passed = completed.returncode == 0 and count == 0
    return {
        "command": ["checkMesh", "-allRegions", "-meshQuality"],
        "returnCode": completed.returncode,
        "violations": count,
        "checks": {key: {"violations": 0 if passed else 1} for key in qa},
        "passed": passed,
        "log": log,
    }
