"""Single-tar checkpoint production and canonical resume branches."""
from __future__ import annotations

import json
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openfoam.common.io import sha256_file
from openfoam.common.schema_adapter import validate


REGIONS = ("hot", "cold", "solid")


@dataclass(frozen=True)
class ResumeDecision:
    action: str
    exit_code: int | None = None
    message: str | None = None


def validate_processor_mesh(case: Path, decomp_n: int) -> None:
    expected = {f"processor{index}" for index in range(decomp_n)}
    actual = {path.name for path in case.glob("processor*") if path.is_dir()}
    if actual != expected:
        raise ValueError(f"processor decomposition mismatch: expected {sorted(expected)}, got {sorted(actual)}")
    for processor in sorted(expected):
        for region in REGIONS:
            mesh = case / processor / "constant" / region / "polyMesh"
            if not mesh.is_dir():
                raise FileNotFoundError(f"processor mesh absent: {mesh}")


def _checkpoint_members(case: Path, time_name: str, decomp_n: int) -> list[Path]:
    members: set[Path] = set()
    for base in (case / f"processor{index}" for index in range(decomp_n)):
        time_dir = base / time_name
        if not time_dir.is_dir():
            raise FileNotFoundError(f"checkpoint processor time absent: {time_dir}")
        for region in REGIONS:
            region_dir = time_dir / region
            if not region_dir.is_dir():
                raise FileNotFoundError(f"checkpoint region absent: {region_dir}")
            if any(path.name.endswith((".tmp", ".part")) for path in region_dir.rglob("*")):
                raise ValueError(f"checkpoint write is incomplete: {region_dir}")
            members.add(region_dir)
    return sorted(members, key=lambda path: path.relative_to(case).as_posix())


def _read_declarations(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    declarations = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    for declaration in declarations:
        validate("checkpoint-declaration", declaration)
    return declarations


def _replace_declarations(path: Path, declarations: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            for declaration in declarations:
                stream.write(json.dumps(declaration, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def produce_checkpoint(
    case: Path,
    output_root: Path,
    *,
    spec_hash: str,
    decomp_n: int,
    iteration: int,
    phys_t: float,
    time_name: str,
) -> dict[str, Any]:
    members = _checkpoint_members(case, time_name, decomp_n)
    if not members:
        raise FileNotFoundError(f"no regional time directories for {time_name}")
    checkpoint_dir = output_root / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    tar_path = checkpoint_dir / f"checkpoint-{iteration}.tar"
    declaration_path = output_root / "checkpoints.ndjson"
    declarations = _read_declarations(declaration_path)
    if any(item["iter"] == iteration for item in declarations):
        raise FileExistsError(f"checkpoint iteration already declared: {iteration}")
    # A previous process may have died after publishing the tar but before the
    # atomic declaration replacement. Such an undeclared tar is safe to rebuild.
    tar_path.unlink(missing_ok=True)
    temporary = checkpoint_dir / f".{tar_path.name}.{os.getpid()}.tmp"
    published = False
    try:
        with tarfile.open(temporary, "w") as archive:
            for member in members:
                archive.add(member, arcname=member.relative_to(case).as_posix(), recursive=True)
        temporary.replace(tar_path)
        published = True
        declaration = {
            "path": tar_path.relative_to(output_root).as_posix(),
            "bytes": tar_path.stat().st_size,
            "sha256": sha256_file(tar_path),
            "iter": iteration,
            "physT": phys_t,
            "specHash": spec_hash,
            "decompN": decomp_n,
            "timeName": time_name,
            "regions": list(REGIONS),
        }
        validate("checkpoint-declaration", declaration)
        declarations.append(declaration)
        _replace_declarations(declaration_path, declarations)
    except Exception:
        if published:
            tar_path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    for previous in sorted(declarations, key=lambda item: item["iter"], reverse=True)[2:]:
        (output_root / previous["path"]).unlink(missing_ok=True)
    return declaration


def resume_checkpoint(case: Path, archive: Path, sidecar: Path, *, spec_hash: str, decomp_n: int) -> ResumeDecision:
    if not sidecar.is_file():
        return ResumeDecision("cold", message="checkpoint sidecar absent")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    validate("checkpoint-declaration", metadata)
    if metadata["specHash"] != spec_hash:
        return ResumeDecision("reject", exit_code=40, message="checkpoint spec hash mismatch")
    if metadata["decompN"] != decomp_n:
        return ResumeDecision("cold", message="checkpoint decomposition mismatch")
    if (
        not archive.is_file()
        or archive.stat().st_size != metadata["bytes"]
        or sha256_file(archive) != metadata["sha256"]
    ):
        return ResumeDecision("cold", message="checkpoint archive absent or corrupt")
    validate_processor_mesh(case, decomp_n)
    with tarfile.open(archive, "r") as source:
        root = case.resolve()
        expected_roots = {
            (f"processor{index}", metadata["timeName"], region)
            for index in range(decomp_n)
            for region in REGIONS
        }
        seen_roots: set[tuple[str, str, str]] = set()
        for member in source.getmembers():
            target = (case / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"unsafe checkpoint member: {member.name}")
            parts = Path(member.name).parts
            member_root = tuple(parts[:3])
            if len(parts) < 3 or member_root not in expected_roots:
                raise ValueError(f"checkpoint contains non-time overlay member: {member.name}")
            seen_roots.add(member_root)
        if seen_roots != expected_roots:
            missing = sorted(expected_roots - seen_roots)
            raise ValueError(f"checkpoint time overlay is incomplete: {missing}")
        source.extractall(case, filter="data")
    for processor, time_name, region in expected_roots:
        if not (case / processor / time_name / region).is_dir():
            raise FileNotFoundError(f"checkpoint region absent after overlay: {processor}/{time_name}/{region}")
    validate_processor_mesh(case, decomp_n)
    return ResumeDecision("resume")
