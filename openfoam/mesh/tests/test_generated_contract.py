from __future__ import annotations

import json
import os
from pathlib import Path

from openfoam.common.schema_adapter import canonical_hash, generated_module_path, validate


def test_generated_python_validator_and_canonical_hash_are_the_contract_source(repo_root):
    contract_root = Path(os.environ.get("GYROX_CONTRACT_ROOT", repo_root / "contract")).resolve()
    assert generated_module_path().is_relative_to(contract_root / "generated/python")

    fixture = json.loads((repo_root / "contract/fixtures/hash/accept/A1-four-spec-baseline.json").read_text())
    geometry = fixture["inputs"]["geometry"]
    result = validate("geometry-spec", geometry)
    assert result.valid is True
    assert canonical_hash("geometry-spec", geometry)[0] == fixture["expected"]["hashes"]["geometry"]
