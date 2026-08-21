from __future__ import annotations

import hashlib
import json
import tarfile

import pytest

from openfoam.solve.checkpoint import produce_checkpoint, resume_checkpoint


@pytest.mark.parametrize("_case", [None], ids=["[SR-10]"])
def test_resume_branches(_case, tmp_path):
    source, output, target = tmp_path / "source", tmp_path / "output", tmp_path / "target"
    for region in ("hot", "cold", "solid"):
        path = source / "processor0/5" / region
        path.mkdir(parents=True)
        (path / "T").write_text("field")
    declaration = produce_checkpoint(source, output, spec_hash="a" * 64, decomp_n=1, iteration=5, phys_t=5.0, time_name="5")
    archive, sidecar = output / declaration["path"], output / "checkpoint.meta.json"
    assert resume_checkpoint(target, archive, sidecar, spec_hash="a" * 64, decomp_n=1).action == "cold"
    sidecar.write_text(json.dumps(declaration))
    assert resume_checkpoint(target, archive, sidecar, spec_hash="b" * 64, decomp_n=1).exit_code == 40
    assert resume_checkpoint(target, archive, sidecar, spec_hash="a" * 64, decomp_n=2).action == "cold"


def test_resume_rejects_incomplete_or_wrong_time_overlay(tmp_path):
    target = tmp_path / "target"
    for region in ("hot", "cold", "solid"):
        (target / "processor0/constant" / region / "polyMesh").mkdir(parents=True)

    for variant in ("missing-region", "wrong-time"):
        source = tmp_path / variant
        archive = tmp_path / f"{variant}.tar"
        regions = ("hot", "cold") if variant == "missing-region" else ("hot", "cold", "solid")
        time_name = "5" if variant == "missing-region" else "6"
        for region in regions:
            field = source / "processor0" / time_name / region / "T"
            field.parent.mkdir(parents=True)
            field.write_text("field")
        with tarfile.open(archive, "w") as output:
            for region in regions:
                output.add(
                    source / "processor0" / time_name / region,
                    arcname=f"processor0/{time_name}/{region}",
                )
        declaration = {
            "path": archive.name, "bytes": archive.stat().st_size,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "iter": 5, "physT": 5.0, "specHash": "a" * 64, "decompN": 1,
            "timeName": "5", "regions": ["hot", "cold", "solid"],
        }
        sidecar = tmp_path / f"{variant}.json"
        sidecar.write_text(json.dumps(declaration))
        with pytest.raises(ValueError, match="incomplete|non-time overlay"):
            resume_checkpoint(target, archive, sidecar, spec_hash="a" * 64, decomp_n=1)
