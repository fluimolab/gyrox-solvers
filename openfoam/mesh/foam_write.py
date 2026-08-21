# Source: ../gyrox/m0/m04/converter/foam_write.py @ aa04b413d3f7e25b91369f7d9b8293aa814e0965
"""OpenFOAM polyMesh 이진/ASCII 기록기 — opencfd 2512 바이트 레이아웃 정합.

경로 C 변환기 전용. 결정론(동일 입력→동일 바이트)이 절대 요건이므로
모든 수치는 little-endian 고정 dtype('<i4' 라벨, '<f8' 스칼라)로 기록한다.

이진 레이아웃 (caseA/constant/*/polyMesh 실측 대조):
  points (vectorField):   <N>\\n( <float64 x,y,z ...> )\\n
  faces  (faceCompactList): <N+1>\\n( <int32 offsets 0,4,..,4N> )\\n <4N>\\n( <int32 refs> )\\n
  owner/neighbour (labelList): <N>\\n( <int32> )\\n   (owner 헤더에 note)
경계(boundary)는 소형이라 ASCII (polyBoundaryMesh).
"""
from __future__ import annotations

import numpy as np

_BANNER = (
    "/*--------------------------------*- C++ -*----------------------------------*\\\n"
    "| =========                 |                                                 |\n"
    "| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |\n"
    "|  \\\\    /   O peration     | Version:  2512                                  |\n"
    "|   \\\\  /    A nd           | Website:  www.openfoam.com                      |\n"
    "|    \\\\/     M anipulation  |                                                 |\n"
    "\\*---------------------------------------------------------------------------*/\n"
)
_SEP = "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n"
_FOOT = "\n// ************************************************************************* //\n"


def _header(cls: str, obj: str, location: str, fmt: str = "binary",
            note: str | None = None) -> bytes:
    lines = [
        _BANNER,
        "FoamFile\n{\n",
        "    version     2.0;\n",
        f"    format      {fmt};\n",
        '    arch        "LSB;label=32;scalar=64";\n',
    ]
    if note is not None:
        lines.append(f'    note        "{note}";\n')
    lines += [
        f"    class       {cls};\n",
        f'    location    "{location}";\n',
        f"    object      {obj};\n",
        "}\n",
        _SEP,
    ]
    return "".join(lines).encode()


def _bin_list(arr: np.ndarray) -> bytes:
    """<count>\\n( <raw bytes> )\\n"""
    return f"\n{arr.shape[0]}\n(".encode() + arr.tobytes() + b")\n"


def write_points(path, pts: np.ndarray, location: str, fmt: str = "binary") -> None:
    """pts: (N,3) meters."""
    n = pts.shape[0]
    with open(path, "wb") as f:
        f.write(_header("vectorField", "points", location, fmt))
        if fmt == "binary":
            f.write(_bin_list(np.ascontiguousarray(pts, dtype="<f8")))
        else:
            body = "".join(f"({x:.10g} {y:.10g} {z:.10g})\n" for x, y, z in pts)
            f.write(f"\n{n}\n(\n{body})\n".encode())
        f.write(_FOOT.encode())


def write_faces(path, faces: np.ndarray, location: str, fmt: str = "binary") -> None:
    """faces: (N,4) int point-ids (모든 면이 quad)."""
    n = faces.shape[0]
    with open(path, "wb") as f:
        f.write(_header("faceCompactList" if fmt == "binary" else "faceList",
                        "faces", location, fmt))
        if fmt == "binary":
            offsets = np.arange(0, 4 * (n + 1), 4, dtype="<i4")
            refs = np.ascontiguousarray(faces.reshape(-1), dtype="<i4")
            f.write(_bin_list(offsets))
            f.write(_bin_list(refs))
        else:
            body = "".join(f"4({a} {b} {c} {d})\n" for a, b, c, d in faces)
            f.write(f"\n{n}\n(\n{body})\n".encode())
        f.write(_FOOT.encode())


def write_labellist(path, arr: np.ndarray, obj: str, location: str,
                    note: str | None = None, fmt: str = "binary") -> None:
    n = arr.shape[0]
    with open(path, "wb") as f:
        f.write(_header("labelList", obj, location, fmt, note=note))
        if fmt == "binary":
            f.write(_bin_list(np.ascontiguousarray(arr, dtype="<i4")))
        else:
            body = "".join(f"{v}\n" for v in arr)
            f.write(f"\n{n}\n(\n{body})\n".encode())
        f.write(_FOOT.encode())


def write_boundary(path, patches: list[dict], location: str) -> None:
    """patches: [{name,type,nFaces,startFace, inGroups?, sampleRegion?, samplePatch?,
                  neighbourPatch?}]  — polyBoundaryMesh ASCII."""
    parts = [_header("polyBoundaryMesh", "boundary", location, fmt="ascii").decode()]
    parts.append(f"\n{len(patches)}\n(\n")
    for p in patches:
        parts.append(f"    {p['name']}\n    {{\n")
        parts.append(f"        type            {p['type']};\n")
        if "inGroups" in p:
            g = p["inGroups"]
            parts.append(f"        inGroups        {len(g)}({' '.join(g)});\n")
        parts.append(f"        nFaces          {p['nFaces']};\n")
        parts.append(f"        startFace       {p['startFace']};\n")
        if p["type"] == "mappedWall":
            parts.append("        sampleMode      nearestPatchFace;\n")
            parts.append(f"        sampleRegion    {p['sampleRegion']};\n")
            parts.append(f"        samplePatch     {p['samplePatch']};\n")
        if p["type"] == "cyclic":
            parts.append(f"        neighbourPatch  {p['neighbourPatch']};\n")
        if p["type"] == "cyclicAMI":
            parts.append(f"        neighbourPatch  {p['neighbourPatch']};\n")
            parts.append("        transform       translational;\n")
            sv = p["separationVector"]
            parts.append(f"        separationVector ({sv[0]:.10g} {sv[1]:.10g} {sv[2]:.10g});\n")
            # 비순응 경계(≈결손율)의 uncovered face 를 그레이스풀 처리 → 0/0 FPE 방지.
            # 이 대조 케이스의 결손 자체가 옵션 A 비가용 증거이며 lowWeightCorrection 은
            # 그 결손 면을 벽처럼 취급(seam 대조에는 무영향, 솔브 안정화용).
            parts.append("        lowWeightCorrection 0.2;\n")
        parts.append("    }\n")
    parts.append(")\n")
    parts.append(_FOOT)
    with open(path, "w") as f:
        f.write("".join(parts))


def write_region_properties(path, fluids: list[str], solids: list[str]) -> None:
    parts = [_header("dictionary", "regionProperties", "constant", fmt="ascii").decode()]
    parts.append("\nregions\n(\n")
    parts.append(f"    fluid ({' '.join(fluids)})\n")
    parts.append(f"    solid ({' '.join(solids)})\n")
    parts.append(");\n")
    parts.append(_FOOT)
    with open(path, "w") as f:
        f.write("".join(parts))

