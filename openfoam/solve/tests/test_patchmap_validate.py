from __future__ import annotations

import copy

import pytest

from openfoam.common.schema_adapter import ContractValidationError, validate
from openfoam.solve.runner import map_process_exit


@pytest.mark.parametrize("_case", [None], ids=["[SR-11]"])
def test_patch_map_revalidation(_case, patch_map):
    invalid = copy.deepcopy(patch_map)
    invalid[0]["patchName"] = "wrong"
    with pytest.raises(ContractValidationError):
        validate("patch-map", invalid)
    assert map_process_exit(30) == ("INVALID_INPUT", 30)
