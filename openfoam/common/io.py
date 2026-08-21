"""Strict `/work` filesystem and contract helpers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .schema_adapter import canonical_hash, validate


WORK_ROOT = Path(os.environ.get("GYROX_WORK_ROOT", "/work"))


def load_request(work_root: Path = WORK_ROOT, expected_stage: str | None = None) -> dict[str, Any]:
    request = json.loads((work_root / "request.json").read_text(encoding="utf-8"))
    validate("work-request", request)
    if expected_stage is not None and request["stage"] != expected_stage:
        raise ValueError(f"expected stage {expected_stage!r}, got {request['stage']!r}")
    return request


def safe_input_path(work_root: Path, relative: str) -> Path:
    base = (work_root / "input").resolve()
    path = (base / relative).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"input path escapes /work/input: {relative!r}")
    return path


def find_input(request: dict[str, Any], work_root: Path, kinds: Iterable[str], *, suffix: str | None = None) -> Path:
    accepted = set(kinds)
    for item in request["inputs"]:
        if item["kind"] in accepted or (suffix and item["path"].endswith(suffix)):
            path = safe_input_path(work_root, item["path"])
            if not path.exists():
                raise FileNotFoundError(path)
            return path
    raise ValueError(f"required input not declared: {sorted(accepted)}")


def read_json_input(request: dict[str, Any], work_root: Path, kinds: Iterable[str]) -> dict[str, Any]:
    return json.loads(find_input(request, work_root, kinds).read_text(encoding="utf-8"))


def read_canonical_spec_input(
    request: dict[str, Any], work_root: Path, slot: str, kinds: Iterable[str],
) -> dict[str, Any]:
    """Read a canonical spec and bind its generated JCS hash to request.specs."""
    document = read_json_input(request, work_root, kinds)
    try:
        reference = request["specs"][slot]
        kind = document["kind"]
    except KeyError as exc:
        raise ValueError(f"canonical spec reference absent: {slot}") from exc
    if reference["kind"] != kind:
        raise ValueError(
            f"canonical spec kind mismatch for {slot}: request={reference['kind']}, document={kind}"
        )
    actual_hash, _canonical = canonical_hash(kind, document)
    if actual_hash != reference["hash"]:
        raise ValueError(
            f"canonical spec hash mismatch for {slot}: request={reference['hash']}, actual={actual_hash}"
        )
    return document


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def manifest_entry(path: Path, output_root: Path, kind: str, media_type: str, **extra: Any) -> dict[str, Any]:
    result = {
        "kind": kind,
        "path": path.relative_to(output_root).as_posix(),
        "mediaType": media_type,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    result.update(extra)
    return result
