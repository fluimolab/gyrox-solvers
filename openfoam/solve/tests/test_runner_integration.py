from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

from openfoam.common.progress import ProgressWriter
from openfoam.common.schema_adapter import canonical_hash
from openfoam.solve.renderer import render_case
from openfoam.solve.runner import _run_solver_with_checkpoints


def _write_tool(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


def _tools(tmp_path: Path, fake_solver: Path) -> Path:
    tools = tmp_path / "bin"
    tools.mkdir()
    _write_tool(tools / "decomposePar", """#!/usr/bin/env python3
import sys
from pathlib import Path
case = Path(sys.argv[sys.argv.index('-case') + 1])
for region in ('hot', 'cold', 'solid'):
    mesh = case / 'processor0/constant' / region / 'polyMesh'
    mesh.mkdir(parents=True, exist_ok=True)
    (mesh / 'owner').write_text('nCells: 1\\n')
""")
    _write_tool(tools / "reconstructPar", "#!/usr/bin/env python3\n")
    _write_tool(tools / "mpirun", f"""#!/usr/bin/env python3
import os, sys
case = sys.argv[sys.argv.index('-case') + 1]
os.execv(sys.executable, [sys.executable, {str(fake_solver)!r}, case])
""")
    return tools


def _prepare_work(tmp_path: Path, solve_document, patch_map, *, checkpoint: str | None = None) -> Path:
    work = tmp_path / "work"
    inputs = work / "input"
    mesh = inputs / "mesh"
    for region in ("hot", "cold", "solid"):
        poly = mesh / "constant" / region / "polyMesh"
        poly.mkdir(parents=True, exist_ok=True)
        (poly / "owner").write_text("nCells: 1\n")
    (inputs / "solve.json").write_text(json.dumps(solve_document))
    (inputs / "patch-map.json").write_text(json.dumps(patch_map))
    hashes = {name: char * 64 for name, char in zip(("geometry", "discretization", "solve", "post"), "abcd")}
    hashes["solve"] = canonical_hash("solve-spec", solve_document)[0]
    manifest = {
        "schemaVersion": 1,
        "specs": {name: {"kind": f"{name}-spec", "hash": digest} for name, digest in hashes.items()},
        "executorPolicy": {
            "maxWallClockSec": 3600, "resourceClass": "small",
            "checkpointIntervalSec": 900, "judgmentSlackIters": 50,
        },
    }
    (inputs / "manifest.json").write_text(json.dumps(manifest))
    declared = [
        {"kind": "solve-spec", "hash": hashes["solve"], "path": "solve.json"},
        {"kind": "patch-map", "hash": "e" * 64, "path": "patch-map.json"},
        {"kind": "run-manifest", "hash": "f" * 64, "path": "manifest.json"},
        {"kind": "mesh", "hash": "1" * 64, "path": "mesh"},
    ]
    if checkpoint is not None:
        declared.append({"kind": "checkpoint", "hash": "2" * 64, "path": "checkpoint.tar"})
        archive = inputs / "checkpoint.tar"
        source = tmp_path / "checkpoint-source"
        for region in ("hot", "cold", "solid"):
            field = source / "processor0/20" / region / "T"
            field.parent.mkdir(parents=True, exist_ok=True)
            field.write_text("checkpoint field")
        with tarfile.open(archive, "w") as output:
            for region in ("hot", "cold", "solid"):
                output.add(source / "processor0/20" / region, arcname=f"processor0/20/{region}")
        spec_hash = hashes["solve"] if checkpoint != "spec-mismatch" else "9" * 64
        decomp_n = 2 if checkpoint == "decomp-mismatch" else 1
        sidecar = {
            "path": "checkpoint.tar", "bytes": archive.stat().st_size,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "iter": 20,
            "physT": 20.0, "specHash": spec_hash, "decompN": decomp_n,
            "timeName": "20", "regions": ["hot", "cold", "solid"],
        }
        if checkpoint != "no-sidecar":
            (inputs / "checkpoint.meta.json").write_text(json.dumps(sidecar))
    request = {
        "contractVersion": 1, "stage": "solve",
        "specs": {name: {"kind": f"{name}-spec", "hash": digest} for name, digest in hashes.items()},
        "inputs": declared,
        "limits": {"cpuCores": 1, "memBytes": 1024 * 1024, "checkpointIntervalSec": 900},
    }
    (work / "request.json").write_text(json.dumps(request))
    return work


def _runner_env(repo_root: Path, tools: Path, work: Path, mode: str) -> dict[str, str]:
    return dict(
        os.environ,
        PATH=f"{tools}:{os.environ['PATH']}",
        PYTHONPATH=str(repo_root),
        GYROX_WORK_ROOT=str(work),
        FAKE_OPENFOAM_MODE=mode,
    )


def test_actual_runner_progress_sigterm_final_checkpoint(repo_root, tmp_path, solve_document, patch_map):
    fake = Path(__file__).with_name("fake_openfoam_solver.py")
    tools = _tools(tmp_path, fake)
    work = _prepare_work(tmp_path, solve_document, patch_map)
    process = subprocess.Popen(
        [sys.executable, "-m", "openfoam.solve.runner"], cwd=repo_root,
        env=_runner_env(repo_root, tools, work, "slow"), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    assert process.stdout is not None
    events = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        event = json.loads(line)
        events.append(event)
        if event["type"] == "progress" and event["payload"]["iter"] >= 20:
            process.send_signal(signal.SIGTERM)
            break
    tail, _ = process.communicate(timeout=15)
    events.extend(json.loads(line) for line in tail.splitlines() if line.strip())
    assert process.returncode == 143
    checkpoint_events = [event for event in events if event["type"] == "checkpoint"]
    assert checkpoint_events
    declaration = checkpoint_events[-1]["payload"]
    assert set(declaration) == {"path", "bytes", "sha256", "iter", "physT", "specHash", "decompN", "timeName", "regions"}
    assert declaration["iter"] >= 20
    archive = work / "output" / declaration["path"]
    with tarfile.open(archive) as saved:
        names = saved.getnames()
    assert all(any(name.startswith(f"processor0/{declaration['timeName']}/{region}") for name in names) for region in ("hot", "cold", "solid"))
    assert json.loads((work / "output/result.json").read_text())["outcome"] == "CANCELED"


@pytest.mark.parametrize("checkpoint,expected", [
    ("valid", (0, "SUCCEEDED")),
    ("spec-mismatch", (40, "INVALID_INPUT")),
    ("no-sidecar", (0, "SUCCEEDED")),
    ("decomp-mismatch", (0, "SUCCEEDED")),
])
def test_runner_resume_and_cold_branches(checkpoint, expected, repo_root, tmp_path, solve_document, patch_map):
    fake = Path(__file__).with_name("fake_openfoam_solver.py")
    tools = _tools(tmp_path, fake)
    work = _prepare_work(tmp_path, solve_document, patch_map, checkpoint=checkpoint)
    completed = subprocess.run(
        [sys.executable, "-m", "openfoam.solve.runner"], cwd=repo_root,
        env=_runner_env(repo_root, tools, work, "complete"), capture_output=True,
        text=True, timeout=30,
    )
    result = json.loads((work / "output/result.json").read_text())
    assert (completed.returncode, result["outcome"]) == expected, completed.stdout + completed.stderr
    progress_iters = [
        event["payload"]["iter"]
        for event in (json.loads(line) for line in completed.stdout.splitlines() if line.strip())
        if event["type"] == "progress"
    ]
    if checkpoint == "valid":
        assert progress_iters[0] == 21
        assert (work / "output/case/processor0/constant/hot/polyMesh").is_dir()
        assert (work / "output/case/processor0/20/hot/T").is_file()
        summary = json.loads((work / "output/summary.json").read_text())
        assert summary["iterations"] == 550
        assert summary["converged"] is True
    elif checkpoint in {"no-sidecar", "decomp-mismatch"}:
        assert progress_iters[0] == 1
        assert not (work / "output/case/processor0/20/hot/T").exists()


def test_runner_rejects_solve_document_hash_mismatch(repo_root, tmp_path, solve_document, patch_map):
    fake = Path(__file__).with_name("fake_openfoam_solver.py")
    tools = _tools(tmp_path, fake)
    work = _prepare_work(tmp_path, solve_document, patch_map)
    request_path = work / "request.json"
    request = json.loads(request_path.read_text())
    request["specs"]["solve"]["hash"] = "9" * 64
    request_path.write_text(json.dumps(request))
    completed = subprocess.run(
        [sys.executable, "-m", "openfoam.solve.runner"], cwd=repo_root,
        env=_runner_env(repo_root, tools, work, "complete"), capture_output=True,
        text=True, timeout=30,
    )
    result = json.loads((work / "output/result.json").read_text())
    assert (completed.returncode, result["outcome"], result["exitCode"]) == (30, "INVALID_INPUT", 30)
    assert "canonical spec hash mismatch for solve" in result["warnings"][0]


def test_two_intervals_write_distinct_atomic_checkpoints(tmp_path, solve_document, patch_map, monkeypatch):
    case, output = tmp_path / "case", tmp_path / "output"
    render_case(solve_document, patch_map, case, cores=1, write_interval=500)
    for region in ("hot", "cold", "solid"):
        mesh = case / "processor0/constant" / region / "polyMesh"
        mesh.mkdir(parents=True)
    monkeypatch.setenv("FAKE_OPENFOAM_MODE", "slow")
    stop = threading.Event()
    result: list[int] = []
    command = [sys.executable, str(Path(__file__).with_name("fake_openfoam_solver.py")), str(case)]
    thread = threading.Thread(target=lambda: result.append(_run_solver_with_checkpoints(
        command, case, output, ProgressWriter(), interval=1, spec_hash="a" * 64,
        cores=1, max_iters=100000, terminate_requested=stop, write_grace=3,
    )))
    thread.start()
    declarations = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        path = output / "checkpoints.ndjson"
        if path.is_file():
            declarations = [json.loads(line) for line in path.read_text().splitlines()]
            if len(declarations) >= 2:
                break
        time.sleep(0.05)
    stop.set()
    thread.join(timeout=10)
    assert result == [143]
    assert len(declarations) >= 2
    assert declarations[0]["iter"] < declarations[1]["iter"]
    assert declarations[0]["path"] != declarations[1]["path"]
    for declaration in declarations[:2]:
        with tarfile.open(output / declaration["path"]) as archive:
            names = archive.getnames()
        assert all(
            any(name.startswith(f"processor0/{declaration['timeName']}/{region}") for name in names)
            for region in ("hot", "cold", "solid")
        )
