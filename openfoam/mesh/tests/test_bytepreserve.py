from __future__ import annotations

import hashlib

import pytest

from openfoam.mesh.labels_to_foam import convert


@pytest.mark.parametrize("_case", [None], ids=["[MC-01]"])
def test_byte_preservation(_case, tmp_path, gyrox_root):
    labels = gyrox_root / "m0/fixtures/gyroid-30-4-0.4/labels.vti"
    if not labels.is_file():
        pytest.fail(f"required CL2 labels fixture is absent: {labels}", pytrace=False)
    actual_input_hash = hashlib.sha256(labels.read_bytes()).hexdigest()
    assert actual_input_hash == "919d7f52fce4738bd8368e915ec4772cbe2d69ad848e55a1b230676d6dcb1388"
    case = tmp_path / "case"
    report = convert(str(labels), str(case), fmt="binary")
    expected = gyrox_root / "m0/m04/runs/phaseC/mesh/CL2/patch-map.json"
    assert (case / "patch-map.json").read_bytes() == expected.read_bytes()
    assert report["artifact_sha256"]["combined"] == "9f3eceb5c7b9443bcfe9253b0bdfb91dd9571c4331d7b3d022dfc0cff36236c1"
