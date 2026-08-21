"""checkMesh invocation and fail-closed quality verdict."""
from __future__ import annotations

import re
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
    (system / "meshQualityDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object meshQualityDict; }\n"
        "meshQualityControls\n{\n" + values + "\n}\n", encoding="utf-8")
    (system / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application checkMesh;\n", encoding="utf-8")


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
