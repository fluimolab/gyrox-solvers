"""Load and expose the generated Gyrox Python contract API.

The contract tree is supplied by PR-SP1. Product images use
``/opt/solvers/contract``; local stacked-branch tests point
``GYROX_CONTRACT_ROOT`` at the SP1 worktree. There is deliberately no raw
JSON-Schema fallback here: a missing generated export is a packaging error.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any


_PACKAGE_NAME = "_gyrox_generated_contract"


def _contract_roots() -> tuple[Path, ...]:
    configured = os.environ.get("GYROX_CONTRACT_ROOT")
    here = Path(__file__).resolve()
    candidates = (
        Path(configured) if configured else None,
        here.parents[2] / "contract",
        Path("/opt/solvers/contract"),
    )
    roots: list[Path] = []
    for candidate in candidates:
        if candidate is not None and candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


@lru_cache(maxsize=1)
def _generated() -> ModuleType:
    for root in _contract_roots():
        package = root / "generated/python"
        init = package / "__init__.py"
        if not init.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            _PACKAGE_NAME, init, submodule_search_locations=[str(package)],
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[_PACKAGE_NAME] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(_PACKAGE_NAME, None)
            raise
        return module
    searched = ", ".join(str(root / "generated/python/__init__.py") for root in _contract_roots())
    raise ImportError(f"generated Gyrox Python contract is absent; searched: {searched}")


ContractValidationError = _generated().ContractValidationError
ValidationResult = _generated().ValidationResult


def validate(kind: str, instance: Any, version: int = 1) -> Any:
    """Return the generated validation result, raising on invalid documents."""
    if version != 1:
        raise ValueError(f"unsupported contract schema version: {version}")
    result = _generated().validate(kind, instance, "canonical")
    if not result.valid:
        _generated().assert_valid(kind, instance, "canonical")
    return result


def assert_valid(kind: str, instance: Any, version: int = 1) -> None:
    if version != 1:
        raise ValueError(f"unsupported contract schema version: {version}")
    _generated().assert_valid(kind, instance, "canonical")


def canonical_hash(kind: str, instance: Any) -> tuple[str, str]:
    return _generated().canonical_hash(kind, instance)


def generated_module_path() -> Path:
    """Expose provenance for drift/consumer tests without duplicating loading."""
    return Path(_generated().__file__).resolve()
