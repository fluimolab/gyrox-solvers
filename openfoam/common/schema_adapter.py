"""Thin JSON-Schema adapter.

The bridge currently exports schemas but no Python validators.  Keeping schema
lookup and jsonschema behind this module makes the generated validator a
drop-in replacement when it arrives.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema


class ContractValidationError(ValueError):
    """Raised when a consumed or produced contract document is invalid."""


def _schema_roots() -> tuple[Path, ...]:
    here = Path(__file__).resolve()
    configured = os.environ.get("GYROX_SCHEMA_DIR")
    candidates = [
        Path(configured) if configured else None,
        here.parents[2] / "contract" / "schemas",
        here.parents[3] / "gyrox" / "packages" / "contracts" / "schemas",
        Path("/opt/solvers/contract/schemas"),
    ]
    return tuple(path for path in candidates if path is not None)


@lru_cache(maxsize=None)
def load_schema(kind: str, version: int = 1) -> dict[str, Any]:
    filename = f"{kind}.v{version}.schema.json"
    for root in _schema_roots():
        path = root / filename
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    searched = ", ".join(str(root / filename) for root in _schema_roots())
    raise FileNotFoundError(f"contract schema not found: {searched}")


def validate(kind: str, instance: Any, version: int = 1) -> None:
    schema = load_schema(kind, version)
    try:
        jsonschema.Draft202012Validator(schema).validate(instance)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ContractValidationError(f"{kind}.v{version} at {where}: {exc.message}") from exc
