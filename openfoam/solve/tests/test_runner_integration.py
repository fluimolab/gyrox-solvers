from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from functools import partial
from pathlib import Path

import pytest

from openfoam.common.progress import ProgressWriter
from openfoam.common.schema_adapter import canonical_hash
from openfoam.solve.renderer import render_case
import openfoam.solve.runner as solve_runner
from openfoam.solve.runner import (
    DEFAULT_CANCEL_WRITE_GRACE_SEC,
    _cancel_write_grace_sec,
    _run_solver_with_checkpoints,
    map_process_exit,
)


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
    _write_tool(tools / "foamToVTK", """#!/usr/bin/env python3
import sys
from pathlib import Path
case = Path(sys.argv[sys.argv.index('-case') + 1])
internal = case / 'VTK/hot/case_550/internal.vtu'
internal.parent.mkdir(parents=True, exist_ok=True)
internal.write_text('<VTKFile/>')
""")
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


def _block_mesh_dict(region: str) -> str:
    bounds = {
        "hot": (0.0, 3.0, "hot_inlet", "hot_outlet", "hot_wall", "hot_to_solid", "solid", "solid_to_hot"),
        "solid": (3.0, 6.0, None, None, "solid_external", "solid_to_cold", "cold", "cold_to_solid"),
        "cold": (6.0, 9.0, "cold_outlet", "cold_inlet", "cold_wall", "cold_to_solid", "solid", "solid_to_cold"),
    }
    y0, y1, inlet, outlet, wall, upper, upper_region, upper_patch = bounds[region]
    if region == "solid":
        lower = "solid_to_hot"
        lower_region = "hot"
        lower_patch = "hot_to_solid"
    else:
        lower = wall
        lower_region = None
        lower_patch = None

    def mapped(name: str, neighbour_region: str, neighbour_patch: str, face: str) -> str:
        return f"""
    {name}
    {{
        type mappedWall;
        sampleMode nearestPatchFace;
        sampleRegion {neighbour_region};
        samplePatch {neighbour_patch};
        faces ({face});
    }}"""

    patches = []
    if inlet is not None:
        patches.extend([
            f"{inlet} {{ type patch; faces ((0 4 7 3)); }}",
            f"{outlet} {{ type patch; faces ((1 2 6 5)); }}",
        ])
    if lower_region is None:
        wall_faces = "((0 1 5 4) (0 3 2 1) (4 5 6 7))"
        patches.append(f"{wall} {{ type wall; faces {wall_faces}; }}")
    else:
        patches.append(mapped(lower, lower_region, lower_patch, "(0 1 5 4)"))
        patches.append(f"{wall} {{ type wall; faces ((0 4 7 3) (1 2 6 5) (0 3 2 1) (4 5 6 7)); }}")
    patches.append(mapped(upper, upper_region, upper_patch, "(3 7 6 2)"))

    return f"""FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    object blockMeshDict;
}}
convertToMeters 0.001;
vertices
(
    (0 {y0} 0) (9 {y0} 0) (9 {y1} 0) (0 {y1} 0)
    (0 {y0} 9) (9 {y0} 9) (9 {y1} 9) (0 {y1} 9)
);
// Three 36x12x36 slabs form the promotion-plan EXT-A3 envelope and total
// cell count: 9 mm cubed at 0.25 mm pitch, 3 * 15,552 = 46,656 cells.
blocks (hex (0 1 2 3 4 5 6 7) (36 12 36) simpleGrading (1 1 1));
edges ();
boundary
(
    {''.join(patches)}
);
mergePatchPairs ();
"""


def _prepare_small_cht_work(tmp_path: Path, solve_document, patch_map, *, cores: int = 2) -> Path:
    work = _prepare_work(tmp_path, solve_document, patch_map)
    mesh = work / "input/mesh"
    shutil.rmtree(mesh)
    (mesh / "constant").mkdir(parents=True)
    (mesh / "constant/regionProperties").write_text("""FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object regionProperties;
}
regions (fluid (hot cold) solid (solid));
""")
    (mesh / "system").mkdir()
    (mesh / "system/controlDict").write_text("""FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object controlDict;
}
application blockMesh;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 1;
deltaT 1;
writeControl timeStep;
writeInterval 1;
""")
    for region in ("hot", "cold", "solid"):
        system = mesh / "system" / region
        system.mkdir(parents=True)
        (system / "blockMeshDict").write_text(_block_mesh_dict(region))
        completed = subprocess.run(
            ["blockMesh", "-case", str(mesh), "-region", region],
            capture_output=True, text=True, check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
    request_path = work / "request.json"
    request = json.loads(request_path.read_text())
    request["limits"]["cpuCores"] = cores
    request_path.write_text(json.dumps(request))
    return work


def test_cancel_write_grace_uses_d_d10_default_and_environment(monkeypatch):
    monkeypatch.delenv("GYROX_CANCEL_WRITE_GRACE_SEC", raising=False)
    assert _cancel_write_grace_sec() == DEFAULT_CANCEL_WRITE_GRACE_SEC == 600.0
    monkeypatch.setenv("GYROX_CANCEL_WRITE_GRACE_SEC", "17.5")
    assert _cancel_write_grace_sec() == 17.5
    monkeypatch.setenv("GYROX_CANCEL_WRITE_GRACE_SEC", "nan")
    with pytest.raises(ValueError, match="positive finite"):
        _cancel_write_grace_sec()


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
    assert 20 <= declaration["iter"] <= 30
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
        assert (work / "scratch/case/processor0/constant/hot/polyMesh").is_dir()
        assert (work / "scratch/case/processor0/20/hot/T").is_file()
        summary = json.loads((work / "output/summary.json").read_text())
        assert summary["iterations"] == 550
        assert summary["converged"] is True
    elif checkpoint in {"no-sidecar", "decomp-mismatch"}:
        assert progress_iters[0] == 1
        assert not (work / "scratch/case/processor0/20/hot/T").exists()


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
    declarations = [json.loads(line) for line in (output / "checkpoints.ndjson").read_text().splitlines()]
    retained = sorted(declarations, key=lambda item: item["iter"], reverse=True)[:2]
    assert {path.name for path in (output / "checkpoints").glob("*.tar")} == {
        Path(declaration["path"]).name for declaration in retained
    }
    for declaration in retained:
        with tarfile.open(output / declaration["path"]) as archive:
            names = archive.getnames()
        assert all(
            any(name.startswith(f"processor0/{declaration['timeName']}/{region}") for name in names)
            for region in ("hot", "cold", "solid")
        )


@pytest.mark.parametrize("mode", [
    "partial-exit",
    "partial-kill",
    "partial-timeout",
    pytest.param("duplicate", marks=pytest.mark.realtime_cancel),
])
def test_non_graceful_or_duplicate_write_never_declares_checkpoint(
    mode, tmp_path, solve_document, patch_map, monkeypatch, capsys,
):
    case, output = tmp_path / "case", tmp_path / "output"
    render_case(solve_document, patch_map, case, cores=1, write_interval=500)
    for region in ("hot", "cold", "solid"):
        mesh = case / "processor0/constant" / region / "polyMesh"
        mesh.mkdir(parents=True)
    if mode == "duplicate":
        for region in ("hot", "cold", "solid"):
            field = case / "processor0/10" / region / "T"
            field.parent.mkdir(parents=True)
            field.write_text("previous complete field")
    monkeypatch.setenv("FAKE_OPENFOAM_MODE", mode)
    command = [sys.executable, str(Path(__file__).with_name("fake_openfoam_solver.py")), str(case)]

    result = _run_solver_with_checkpoints(
        command, case, output, ProgressWriter(), interval=0, spec_hash="a" * 64,
        cores=1, max_iters=100000, write_grace=0.2,
    )

    assert result != 0
    assert not list((output / "checkpoints").glob("*.tar"))
    assert not (output / "checkpoints.ndjson").exists()
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert not [event for event in events if event["type"] == "checkpoint"]
    warnings = [event for event in events if event["type"] == "log"]
    assert warnings
    assert all(event["payload"] == {"level": "warn", "code": "checkpoint-io-error"} for event in warnings)


def test_pinned_cht_product_entrypoint_pid1_sigterm_writes_checkpoint(
    tmp_path, solve_document, patch_map, monkeypatch, capsys,
):
    """Cancel the real pinned multi-region product path through its PID-1 handler."""
    if shutil.which("chtMultiRegionSimpleFoam") is None or shutil.which("blockMesh") is None:
        pytest.skip("requires the pinned OpenFOAM runtime image")
    assert os.environ.get("WM_PROJECT_VERSION") == "v2512"
    assert os.getpid() == 1, "real product cancellation test must run as container PID 1"
    solve_document["payload"]["limits"]["maxIters"] = 100000
    work = _prepare_small_cht_work(tmp_path, solve_document, patch_map)

    product_execute = solve_runner.execute
    monkeypatch.setattr(solve_runner, "execute", partial(product_execute, work))
    monkeypatch.delenv("GYROX_CANCEL_WRITE_GRACE_SEC", raising=False)

    class CancelAtWindow(ProgressWriter):
        observed_iter: int | None = None

        def emit(self, event_type, payload, **envelope_fields):
            event = super().emit(event_type, payload, **envelope_fields)
            if event_type == "progress" and self.observed_iter is None and 20 <= payload["iter"] <= 29:
                type(self).observed_iter = payload["iter"]
                os.kill(os.getpid(), signal.SIGTERM)
            return event

    monkeypatch.setattr(solve_runner, "ProgressWriter", CancelAtWindow)

    previous_handler = signal.getsignal(signal.SIGTERM)
    try:
        result = solve_runner.main()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)

    assert CancelAtWindow.observed_iter is not None, "chtMultiRegionSimpleFoam missed cancellation window"
    assert 20 <= CancelAtWindow.observed_iter <= 29
    assert result == 143
    final_result = json.loads((work / "output/result.json").read_text())
    assert (final_result["outcome"], final_result["exitCode"]) == ("CANCELED", 143)

    declarations = [json.loads(line) for line in (work / "output/checkpoints.ndjson").read_text().splitlines()]
    assert len(declarations) == 1
    declaration = declarations[0]
    assert set(declaration) == {
        "path", "bytes", "sha256", "iter", "physT", "specHash",
        "decompN", "timeName", "regions",
    }
    assert declaration["decompN"] == 2
    assert 20 <= declaration["iter"] <= 30
    assert float(declaration["timeName"]) > 0

    archive_path = work / "output" / declaration["path"]
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == declaration["sha256"]
    with tarfile.open(archive_path) as archive:
        members = archive.getnames()
    for processor in range(declaration["decompN"]):
        for region in ("hot", "cold", "solid"):
            prefix = f"processor{processor}/{declaration['timeName']}/{region}/"
            assert any(name.startswith(prefix) for name in members)
    processor0_fields = {
        (parts[2], parts[3])
        for name in members
        if len(parts := Path(name).parts) == 4
        and parts[:2] == ("processor0", declaration["timeName"])
        and parts[2] in {"hot", "cold", "solid"}
    }
    assert len(processor0_fields) >= 9
    assert {("hot", "U"), ("hot", "T"), ("cold", "U"), ("cold", "T"), ("solid", "T")} <= processor0_fields

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert any(event["type"] == "progress" and 20 <= event["payload"]["iter"] <= 30 for event in events)
    checkpoint_events = [event for event in events if event["type"] == "checkpoint"]
    assert len(checkpoint_events) == 1 and checkpoint_events[0]["payload"] == declaration


@pytest.mark.realtime_cancel
def test_pinned_icofoam_pid1_sigterm_waits_for_write_now(tmp_path, capsys):
    """Exercise the real 2512 signal semantics used by the runtime image."""
    if shutil.which("icoFoam") is None or shutil.which("blockMesh") is None:
        pytest.skip("requires the pinned OpenFOAM runtime image")
    assert os.getpid() == 1, "real OpenFOAM cancellation test must run as container PID 1"
    tutorial_root = Path(os.environ["FOAM_TUTORIALS"])
    source = tutorial_root / "incompressible/icoFoam/cavity/cavity"
    case, output = tmp_path / "case", tmp_path / "output"
    shutil.copytree(source, case)
    control = case / "system/controlDict"
    control.write_text(
        control.read_text().replace("endTime         0.5;", "endTime         100000;")
        .replace("writeInterval   20;", "writeInterval   100000;")
    )
    subprocess.run(["blockMesh", "-case", str(case)], check=True, capture_output=True, text=True)
    for region in ("hot", "cold", "solid"):
        (case / "processor0/constant" / region / "polyMesh").mkdir(parents=True)

    wrapper = tmp_path / "ico-write-wrapper.py"
    _write_tool(wrapper, """#!/usr/bin/env python3
import shutil
import subprocess
import sys
from pathlib import Path

case = Path(sys.argv[1])
completed = subprocess.run(["icoFoam", "-case", str(case)])
if completed.returncode == 0:
    times = [path for path in case.iterdir() if path.is_dir() and path.name != "0"]
    times = [path for path in times if path.name.replace(".", "", 1).isdigit()]
    latest = max(times, key=lambda path: float(path.name))
    for region in ("hot", "cold", "solid"):
        target = case / "processor0" / latest.name / region
        target.mkdir(parents=True, exist_ok=True)
        for name in ("U", "p", "phi"):
            shutil.copy2(latest / name, target / name)
sys.exit(completed.returncode)
""")
    requested = threading.Event()
    result: list[int] = []
    previous_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: requested.set())
    thread = threading.Thread(target=lambda: result.append(_run_solver_with_checkpoints(
        [sys.executable, str(wrapper), str(case)], case, output, ProgressWriter(),
        interval=3600, spec_hash="a" * 64, cores=1, max_iters=100000,
        terminate_requested=requested, write_grace=20,
    )))
    try:
        thread.start()
        log_path = case / "logs/cht.log"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if log_path.is_file() and re.search(r"^Time = 2[0-9](?:\.|$)", log_path.read_text(), re.M):
                os.kill(os.getpid(), signal.SIGTERM)
                break
            time.sleep(0.02)
        else:
            pytest.fail("icoFoam did not reach cancellation window 20..29")
        thread.join(timeout=30)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    assert not thread.is_alive()
    assert result == [143]
    assert map_process_exit(result[0]) == ("CANCELED", 143)

    declarations = [json.loads(line) for line in (output / "checkpoints.ndjson").read_text().splitlines()]
    assert len(declarations) == 1
    declaration = declarations[0]
    assert set(declaration) == {
        "path", "bytes", "sha256", "iter", "physT", "specHash",
        "decompN", "timeName", "regions",
    }
    assert 20 <= declaration["iter"] <= 30
    with tarfile.open(output / declaration["path"]) as archive:
        fields = {
            Path(name).name for name in archive.getnames()
            if Path(name).name in {"U", "p", "phi"}
        }
        members = archive.getnames()
    assert fields == {"U", "p", "phi"}
    assert sum(Path(name).name in {"U", "p", "phi"} for name in members) == 9
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert any(event["type"] == "progress" and 20 <= event["payload"]["iter"] <= 30 for event in events)
    checkpoints = [event for event in events if event["type"] == "checkpoint"]
    assert len(checkpoints) == 1 and checkpoints[0]["payload"] == declaration
