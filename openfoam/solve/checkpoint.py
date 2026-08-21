"""Single-tar checkpoint production and canonical resume branches."""
from __future__ import annotations

import json
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


def _checkpoint_members(case: Path, time_name: str) -> list[Path]:
    members: set[Path] = set()
    for base in (case, *sorted(case.glob("processor*"))):
        time_dir = base / time_name
        if not time_dir.is_dir():
            continue
        for region in REGIONS:
            region_dir = time_dir / region
            if not region_dir.is_dir():
                raise FileNotFoundError(f"checkpoint region absent: {region_dir}")
            members.add(region_dir)
    return sorted(members, key=lambda path: path.relative_to(case).as_posix())


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
    members = _checkpoint_members(case, time_name)
    if not members:
        raise FileNotFoundError(f"no regional time directories for {time_name}")
    checkpoint_dir = output_root / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    tar_path = checkpoint_dir / f"checkpoint-{iteration}.tar"
    with tarfile.open(tar_path, "w") as archive:
        for member in members:
            archive.add(member, arcname=member.relative_to(case).as_posix(), recursive=True)
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
    with (output_root / "checkpoints.ndjson").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(declaration, separators=(",", ":")) + "\n")
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
    if not archive.is_file() or sha256_file(archive) != metadata["sha256"]:
        return ResumeDecision("cold", message="checkpoint archive absent or corrupt")
    with tarfile.open(archive, "r") as source:
        root = case.resolve()
        for member in source.getmembers():
            target = (case / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"unsafe checkpoint member: {member.name}")
        source.extractall(case, filter="data")
    return ResumeDecision("resume")
