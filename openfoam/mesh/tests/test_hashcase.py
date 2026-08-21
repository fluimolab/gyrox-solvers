from __future__ import annotations

import hashlib

import pytest

from openfoam.mesh.labels_to_foam import _hash_case


@pytest.mark.parametrize("_case", [None], ids=["[MC-03]"])
def test_combined_hash_rule(_case, tmp_path):
    case = tmp_path
    for region in ("hot", "cold", "solid"):
        mesh = case / "constant" / region / "polyMesh"
        mesh.mkdir(parents=True)
        for index, name in enumerate(("points", "faces", "owner", "neighbour", "boundary")):
            (mesh / name).write_bytes(f"{region}:{index}".encode())
    (case / "constant/regionProperties").write_bytes(b"regions")
    (case / "patch-map.json").write_bytes(b"patches")
    reported = _hash_case(case, {name: True for name in ("hot", "cold", "solid")}, "binary")

    pieces = {}
    for region in ("hot", "cold", "solid"):
        running = hashlib.sha256()
        for name in ("points", "faces", "owner", "neighbour", "boundary"):
            running.update((case / "constant" / region / "polyMesh" / name).read_bytes())
        pieces[region] = running.hexdigest()
    pieces["regionProperties"] = hashlib.sha256((case / "constant/regionProperties").read_bytes()).hexdigest()
    pieces["patch-map.json"] = hashlib.sha256((case / "patch-map.json").read_bytes()).hexdigest()
    preimage = b"".join(key.encode() + pieces[key].encode() for key in sorted(pieces))
    assert reported["combined"] == hashlib.sha256(preimage).hexdigest()
