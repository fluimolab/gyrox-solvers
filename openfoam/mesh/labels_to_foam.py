#!/usr/bin/env python3
# Source: ../gyrox/m0/m04/converter/labels_to_foam.py @ aa04b413d3f7e25b91369f7d9b8293aa814e0965
"""labels.vti (UInt8, D-M0-2: 0=outside/1=hot/2=cold/3=solid) → OpenFOAM 3리전
polyMesh + patch-map.json  (D-M04 Phase A, 경로 C 직결 헥사).

핵심 성질 (proposal §2):
 - 메시 피치 = 라벨 피치 1:1. 라벨≠0 복셀 하나 = 헥사 셀 하나. 리전 = 라벨.
 - 정육면체 헥사 → skew 0 / nonOrtho 0 / 음부피 0 (checkMesh 자명 통과 기대).
 - 결정론: 고정 순회순서(C-order) + 고정 dtype → 동일 라벨은 동일 바이트.

산출:
  <case>/constant/regionProperties
  <case>/constant/{hot,cold,solid}/polyMesh/{points,faces,owner,neighbour,boundary}
  <case>/patch-map.json           (m0/mesh-paths/common/patch-map.schema.json 준수)
  <case>/convert-report.json      (셀·면·패치 카운트, 시간, RSS, 결정론 해시)

패치 명명 (D-M0-7, fixture gyroid-core-30 patches):
  외부: hot_inlet(x=0)/hot_outlet(x=L)/hot_wall,  cold_inlet(x=L)/cold_outlet(x=0)/cold_wall
        (counterflow-x), solid_external
  인터페이스: hot_to_solid↔solid_to_hot, cold_to_solid↔solid_to_cold  (mappedWall 쌍)
  cyclic(옵션, Phase B): <region>_{y,z}{min,max}  (conformal 1:1, x축은 항상 유동/불가)

단위: VTI spacing/origin 은 mm (D-M0-3) → 점 좌표는 m 로 기록 (×1e-3).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1] / "mesh-paths" / "common"))
from vti_io import read_vti_uint8  # noqa: E402
import foam_write as fw  # noqa: E402

# 라벨 값 → 리전
OUTSIDE, HOT, COLD, SOLID = 0, 1, 2, 3
REGIONS = [("hot", HOT), ("cold", COLD), ("solid", SOLID)]
LABEL_NAME = {0: "outside", 1: "hot", 2: "cold", 3: "solid"}

# 방향 코드 (도메인 경계면 노출 방향)
DIRS = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")


class HotColdAdjacency(RuntimeError):
    """hot-cold 직접 면 인접 — V2 위반. 유효 패치 없음 → 하드 실패."""


def _region_patch_plan(region: str, cyclic_axes: frozenset[str],
                       cyclic_type: str = "cyclic", dom_len_m: dict | None = None):
    """리전별 (도메인방향→패치명, 인접라벨→패치명, 패치순서, 패치메타) 계획.

    반환: domain_patch(dict dir→name), adj_patch(dict label→name or 'ERR'),
          order(list[name]), meta(dict name→patch dict template)
    cyclic_type: "cyclic"(conformal seam) | "cyclicAMI"(비순응 라벨 대조용).
    """
    dom_len_m = dom_len_m or {"y": 0.0, "z": 0.0}
    def wall_or_cyclic(axis, side, base_wall):
        # axis in {'y','z'}, side in {'min','max'}
        if axis in cyclic_axes:
            return f"{region}_{axis}{side}"
        return base_wall

    if region == "hot":
        wall = "hot_wall"
        domain = {
            "xmin": "hot_inlet", "xmax": "hot_outlet",
            "ymin": wall_or_cyclic("y", "min", wall),
            "ymax": wall_or_cyclic("y", "max", wall),
            "zmin": wall_or_cyclic("z", "min", wall),
            "zmax": wall_or_cyclic("z", "max", wall),
        }
        adj = {OUTSIDE: wall, SOLID: "hot_to_solid", COLD: "ERR"}
        order = ["hot_to_solid", "hot_inlet", "hot_outlet", "hot_wall"]
        meta = {
            "hot_to_solid": dict(type="mappedWall", inGroups=["wall", "mappedPatch"],
                                 sampleRegion="solid", samplePatch="solid_to_hot"),
            "hot_inlet": dict(type="patch"),
            "hot_outlet": dict(type="patch"),
            "hot_wall": dict(type="wall", inGroups=["wall"]),
        }
    elif region == "cold":
        wall = "cold_wall"
        domain = {
            "xmin": "cold_outlet", "xmax": "cold_inlet",
            "ymin": wall_or_cyclic("y", "min", wall),
            "ymax": wall_or_cyclic("y", "max", wall),
            "zmin": wall_or_cyclic("z", "min", wall),
            "zmax": wall_or_cyclic("z", "max", wall),
        }
        adj = {OUTSIDE: wall, SOLID: "cold_to_solid", HOT: "ERR"}
        order = ["cold_to_solid", "cold_inlet", "cold_outlet", "cold_wall"]
        meta = {
            "cold_to_solid": dict(type="mappedWall", inGroups=["wall", "mappedPatch"],
                                  sampleRegion="solid", samplePatch="solid_to_cold"),
            "cold_inlet": dict(type="patch"),
            "cold_outlet": dict(type="patch"),
            "cold_wall": dict(type="wall", inGroups=["wall"]),
        }
    else:  # solid
        ext = "solid_external"
        domain = {
            "xmin": ext, "xmax": ext,
            "ymin": wall_or_cyclic("y", "min", ext),
            "ymax": wall_or_cyclic("y", "max", ext),
            "zmin": wall_or_cyclic("z", "min", ext),
            "zmax": wall_or_cyclic("z", "max", ext),
        }
        adj = {OUTSIDE: ext, HOT: "solid_to_hot", COLD: "solid_to_cold"}
        order = ["solid_to_hot", "solid_to_cold", "solid_external"]
        meta = {
            "solid_to_hot": dict(type="mappedWall", inGroups=["wall", "mappedPatch"],
                                 sampleRegion="hot", samplePatch="hot_to_solid"),
            "solid_to_cold": dict(type="mappedWall", inGroups=["wall", "mappedPatch"],
                                  sampleRegion="cold", samplePatch="cold_to_solid"),
            "solid_external": dict(type="wall", inGroups=["wall"]),
        }

    # cyclic 패치를 순서 끝에 추가 (min→max 쌍, y 먼저 z)
    for axis in ("y", "z"):
        if axis in cyclic_axes:
            lo, hi = f"{region}_{axis}min", f"{region}_{axis}max"
            order += [lo, hi]
            if cyclic_type == "cyclicAMI":
                L = dom_len_m[axis]
                sv = {"y": (0.0, L, 0.0), "z": (0.0, 0.0, L)}[axis]
                svn = tuple(-c for c in sv)
                meta[lo] = dict(type="cyclicAMI", inGroups=["cyclicAMI"],
                                neighbourPatch=hi, separationVector=sv)
                meta[hi] = dict(type="cyclicAMI", inGroups=["cyclicAMI"],
                                neighbourPatch=lo, separationVector=svn)
            else:
                meta[lo] = dict(type="cyclic", inGroups=["cyclic"], neighbourPatch=hi)
                meta[hi] = dict(type="cyclic", inGroups=["cyclic"], neighbourPatch=lo)
    return domain, adj, order, meta


def build_region(lab: np.ndarray, rv: int, region: str, spacing_mm, origin_mm,
                 cyclic_axes: frozenset[str], cyclic_type: str = "cyclic"):
    """한 리전의 polyMesh 구성요소 계산 → dict(points, faces, owner, neighbour,
    patches, counts)."""
    nz, ny, nx = lab.shape
    VNX, VNY = nx + 1, ny + 1
    M = lab == rv
    labi = lab.astype(np.int16)  # -1 도메인경계 센티넬 수용 (uint8 오버플로 방지)
    ncell = int(M.sum())
    cellid = np.full((nz, ny, nx), -1, dtype=np.int64)
    cellid[M] = np.arange(ncell, dtype=np.int64)  # C-order (k,j,i) — 결정론

    dom_len_m = {"y": ny * spacing_mm[1] * 1e-3, "z": nz * spacing_mm[2] * 1e-3}
    domain, adj, order, meta = _region_patch_plan(region, cyclic_axes,
                                                  cyclic_type, dom_len_m)
    pidx = {name: i for i, name in enumerate(order)}

    int_v, int_own, int_nbr = [], [], []
    bnd_v, bnd_own, bnd_pat = [], [], []

    def vid(ii, jj, kk):
        return kk * (VNY * VNX) + jj * VNX + ii  # 정점 선형 id (kk,jj,ii)

    def classify_bnd(ddir, isdom, otherlab, kk, jj, ii, axis_name):
        """경계면 → 패치 인덱스 배열. hot-cold 인접 시 예외."""
        out = np.empty(otherlab.shape, dtype=np.int32)
        out[isdom] = pidx[domain[ddir]]
        nd = ~isdom
        if nd.any():
            ol = otherlab[nd]
            sub = np.full(ol.shape, -1, dtype=np.int32)
            bad = np.zeros(ol.shape, dtype=bool)
            for L in np.unique(ol):
                name = adj.get(int(L))
                m = ol == L
                if name is None or name == "ERR":
                    bad |= m
                else:
                    sub[m] = pidx[name]
            if bad.any():
                ndidx = np.nonzero(nd)[0]
                bidx = ndidx[bad]
                s = bidx[0]
                raise HotColdAdjacency(
                    f"{region}: 유체-유체 직접 인접 {int(bad.sum())}면 (V2 위반). "
                    f"예: 셀(x={int(ii[s])},y={int(jj[s])},z={int(kk[s])}) "
                    f"{axis_name} 방향 이웃 라벨={int(otherlab[s])}")
            out[nd] = sub
        return out

    # ---------------- X 패밀리 (평면 if=0..nx, 법선 ±x) ----------------
    Lact = np.zeros((nz, ny, nx + 1), bool); Lact[:, :, 1:] = M
    Ract = np.zeros((nz, ny, nx + 1), bool); Ract[:, :, :-1] = M
    # 내부
    kk, jj, ii = np.nonzero(Lact & Ract)
    if ii.size:
        int_own.append(cellid[kk, jj, ii - 1]); int_nbr.append(cellid[kk, jj, ii])
        int_v.append(np.stack([vid(ii, jj, kk), vid(ii, jj + 1, kk),
                               vid(ii, jj + 1, kk + 1), vid(ii, jj, kk + 1)], axis=1))
    # 경계: 활성이 -x쪽(왼쪽), 법선 +x
    kk, jj, ii = np.nonzero(Lact & ~Ract)
    if ii.size:
        bnd_own.append(cellid[kk, jj, ii - 1])
        isdom = ii == nx
        oth = np.where(isdom, -1, labi[kk, jj, np.minimum(ii, nx - 1)])
        bnd_pat.append(classify_bnd("xmax", isdom, oth, kk, jj, ii, "+x"))
        bnd_v.append(np.stack([vid(ii, jj, kk), vid(ii, jj + 1, kk),
                               vid(ii, jj + 1, kk + 1), vid(ii, jj, kk + 1)], axis=1))
    # 경계: 활성이 +x쪽(오른쪽), 법선 -x (역순)
    kk, jj, ii = np.nonzero(Ract & ~Lact)
    if ii.size:
        bnd_own.append(cellid[kk, jj, ii])
        isdom = ii == 0
        oth = np.where(isdom, -1, labi[kk, jj, np.maximum(ii - 1, 0)])
        bnd_pat.append(classify_bnd("xmin", isdom, oth, kk, jj, ii, "-x"))
        bnd_v.append(np.stack([vid(ii, jj, kk), vid(ii, jj, kk + 1),
                               vid(ii, jj + 1, kk + 1), vid(ii, jj + 1, kk)], axis=1))
    del Lact, Ract

    # ---------------- Y 패밀리 (평면 jf=0..ny, 법선 ±y) ----------------
    Lact = np.zeros((nz, ny + 1, nx), bool); Lact[:, 1:, :] = M
    Ract = np.zeros((nz, ny + 1, nx), bool); Ract[:, :-1, :] = M
    kk, jj, ii = np.nonzero(Lact & Ract)
    if ii.size:
        int_own.append(cellid[kk, jj - 1, ii]); int_nbr.append(cellid[kk, jj, ii])
        # +y 법선: (i,jf,k)(i,jf,k+1)(i+1,jf,k+1)(i+1,jf,k)
        int_v.append(np.stack([vid(ii, jj, kk), vid(ii, jj, kk + 1),
                               vid(ii + 1, jj, kk + 1), vid(ii + 1, jj, kk)], axis=1))
    kk, jj, ii = np.nonzero(Lact & ~Ract)  # 활성 -y쪽, 법선 +y
    if ii.size:
        bnd_own.append(cellid[kk, jj - 1, ii])
        isdom = jj == ny
        oth = np.where(isdom, -1, labi[kk, np.minimum(jj, ny - 1), ii])
        bnd_pat.append(classify_bnd("ymax", isdom, oth, kk, jj, ii, "+y"))
        bnd_v.append(np.stack([vid(ii, jj, kk), vid(ii, jj, kk + 1),
                               vid(ii + 1, jj, kk + 1), vid(ii + 1, jj, kk)], axis=1))
    kk, jj, ii = np.nonzero(Ract & ~Lact)  # 활성 +y쪽, 법선 -y (역순)
    if ii.size:
        bnd_own.append(cellid[kk, jj, ii])
        isdom = jj == 0
        oth = np.where(isdom, -1, labi[kk, np.maximum(jj - 1, 0), ii])
        bnd_pat.append(classify_bnd("ymin", isdom, oth, kk, jj, ii, "-y"))
        bnd_v.append(np.stack([vid(ii, jj, kk), vid(ii + 1, jj, kk),
                               vid(ii + 1, jj, kk + 1), vid(ii, jj, kk + 1)], axis=1))
    del Lact, Ract

    # ---------------- Z 패밀리 (평면 kf=0..nz, 법선 ±z) ----------------
    Lact = np.zeros((nz + 1, ny, nx), bool); Lact[1:, :, :] = M
    Ract = np.zeros((nz + 1, ny, nx), bool); Ract[:-1, :, :] = M
    kk, jj, ii = np.nonzero(Lact & Ract)
    if ii.size:
        int_own.append(cellid[kk - 1, jj, ii]); int_nbr.append(cellid[kk, jj, ii])
        # +z 법선: (i,j,kf)(i+1,j,kf)(i+1,j+1,kf)(i,j+1,kf)
        int_v.append(np.stack([vid(ii, jj, kk), vid(ii + 1, jj, kk),
                               vid(ii + 1, jj + 1, kk), vid(ii, jj + 1, kk)], axis=1))
    kk, jj, ii = np.nonzero(Lact & ~Ract)  # 활성 -z쪽, 법선 +z
    if ii.size:
        bnd_own.append(cellid[kk - 1, jj, ii])
        isdom = kk == nz
        oth = np.where(isdom, -1, labi[np.minimum(kk, nz - 1), jj, ii])
        bnd_pat.append(classify_bnd("zmax", isdom, oth, kk, jj, ii, "+z"))
        bnd_v.append(np.stack([vid(ii, jj, kk), vid(ii + 1, jj, kk),
                               vid(ii + 1, jj + 1, kk), vid(ii, jj + 1, kk)], axis=1))
    kk, jj, ii = np.nonzero(Ract & ~Lact)  # 활성 +z쪽, 법선 -z (역순)
    if ii.size:
        bnd_own.append(cellid[kk, jj, ii])
        isdom = kk == 0
        oth = np.where(isdom, -1, labi[np.maximum(kk - 1, 0), jj, ii])
        bnd_pat.append(classify_bnd("zmin", isdom, oth, kk, jj, ii, "-z"))
        bnd_v.append(np.stack([vid(ii, jj, kk), vid(ii, jj + 1, kk),
                               vid(ii + 1, jj + 1, kk), vid(ii + 1, jj, kk)], axis=1))
    del Lact, Ract, cellid

    # ---------------- 내부면 결합 + 상삼각 정렬 (owner→neighbour) --------
    iv = np.concatenate(int_v) if int_v else np.zeros((0, 4), np.int64)
    io = np.concatenate(int_own) if int_own else np.zeros(0, np.int64)
    inb = np.concatenate(int_nbr) if int_nbr else np.zeros(0, np.int64)
    order_i = np.lexsort((inb, io))  # 1차 owner, 2차 neighbour → upper-triangular
    iv, io, inb = iv[order_i], io[order_i], inb[order_i]
    n_internal = io.shape[0]

    # ---------------- 경계면 결합 + 패치별 그룹화 (고정 순서) ------------
    bv = np.concatenate(bnd_v) if bnd_v else np.zeros((0, 4), np.int64)
    bo = np.concatenate(bnd_own) if bnd_own else np.zeros(0, np.int64)
    bp = np.concatenate(bnd_pat) if bnd_pat else np.zeros(0, np.int32)
    order_b = np.argsort(bp, kind="stable")  # 패치 인덱스로 안정 정렬 → 리전 고정순서
    bv, bo, bp = bv[order_b], bo[order_b], bp[order_b]

    # 패치별 카운트/시작면 (비어있는 패치는 제외)
    patch_records, patch_counts = [], {}
    start = n_internal
    for i, name in enumerate(order):
        cnt = int(np.count_nonzero(bp == i))
        patch_counts[name] = cnt
        if cnt == 0:
            continue
        rec = dict(name=name, nFaces=cnt, startFace=start, **meta[name])
        patch_records.append(rec)
        start += cnt

    # ---------------- 정점 dedup → 점 좌표 (m) ----------------
    used = np.zeros((VNY * VNX) * (nz + 1), dtype=bool)
    all_v = np.concatenate([iv.reshape(-1), bv.reshape(-1)]) if (iv.size or bv.size) \
        else np.zeros(0, np.int64)
    used[all_v] = True
    pid_map = np.full(used.shape, -1, dtype=np.int64)
    npoints = int(used.sum())
    pid_map[used] = np.arange(npoints, dtype=np.int64)

    lin = np.nonzero(used)[0]
    kk = lin // (VNY * VNX)
    rem = lin % (VNY * VNX)
    jj = rem // VNX
    ii = rem % VNX
    sx, sy, sz = spacing_mm
    ox, oy, oz = origin_mm
    pts = np.empty((npoints, 3), dtype=np.float64)
    pts[:, 0] = (ii * sx + ox) * 1e-3
    pts[:, 1] = (jj * sy + oy) * 1e-3
    pts[:, 2] = (kk * sz + oz) * 1e-3

    faces_int = pid_map[iv].astype(np.int32) if iv.size else np.zeros((0, 4), np.int32)
    faces_bnd = pid_map[bv].astype(np.int32) if bv.size else np.zeros((0, 4), np.int32)
    faces = np.concatenate([faces_int, faces_bnd]) if (faces_int.size or faces_bnd.size) \
        else np.zeros((0, 4), np.int32)
    owner = np.concatenate([io, bo]).astype(np.int32)
    neighbour = inb.astype(np.int32)

    return dict(
        region=region, ncell=ncell, npoints=npoints,
        n_internal=n_internal, n_boundary=int(bo.shape[0]), nfaces=int(faces.shape[0]),
        points=pts, faces=faces, owner=owner, neighbour=neighbour,
        patches=patch_records, patch_counts=patch_counts,
    )


def validate_cyclic(lab: np.ndarray, cyclic_axes: frozenset[str]) -> dict:
    """cyclic 요청 축에 대해 라벨이 주기적(1:1 conformal)인지 검증.

    conformal cyclic 은 min/max 면의 활성 셀 컬럼이 동일해야 성립. 라벨 전체가
    해당 축으로 주기적(lab[...,0면] == lab[...,반대면 셀])이면 자명 만족.
    """
    nz, ny, nx = lab.shape
    out = {}
    for axis in cyclic_axes:
        if axis == "y":
            lo, hi = lab[:, 0, :], lab[:, ny - 1, :]
        elif axis == "z":
            lo, hi = lab[0, :, :], lab[nz - 1, :, :]
        else:
            raise ValueError(f"cyclic 축은 y/z 만 (x=유동축): {axis}")
        # 각 리전에 대해 min/max 노출 셀 집합 동일성
        ok = True
        detail = {}
        for name, rv in (("hot", HOT), ("cold", COLD), ("solid", SOLID)):
            a = (lo == rv); b = (hi == rv)
            same = bool(np.array_equal(a, b))
            detail[name] = {"min_faces": int(a.sum()), "max_faces": int(b.sum()),
                            "conformal": same}
            ok = ok and same
        out[axis] = {"conformal_1to1": ok, "regions": detail}
    return out


def convert(vti_path: str, case_dir: str, cyclic: frozenset[str] = frozenset(),
            fmt: str = "binary", cyclic_type: str = "cyclic") -> dict:
    t0 = time.time()
    vol = read_vti_uint8(vti_path)
    lab = np.ascontiguousarray(vol._data)  # (nz,ny,nx) uint8
    nz, ny, nx = lab.shape
    hist = np.bincount(lab.reshape(-1), minlength=4)[:4]

    cyc_report = validate_cyclic(lab, cyclic) if cyclic else {}
    # strict cyclic 만 1:1 conformal 강제 (cyclicAMI 는 비순응 허용 — overlap 보간).
    if (cyclic and cyclic_type == "cyclic"
            and not all(v["conformal_1to1"] for v in cyc_report.values())):
        raise RuntimeError(f"cyclic 비순응 라벨 — 1:1 conformal 실패 (cyclicAMI 를 쓰거나 "
                           f"seam 구성 적용): {json.dumps(cyc_report, ensure_ascii=False)}")

    case = Path(case_dir)
    (case / "constant").mkdir(parents=True, exist_ok=True)
    ext = "bin" if fmt == "binary" else "asc"

    region_reports = {}
    patch_map = []
    present = {"hot": False, "cold": False, "solid": False}
    for region, rv in REGIONS:
        if hist[rv] == 0:
            continue
        present[region] = True
        R = build_region(lab, rv, region, vol.spacing, vol.origin, cyclic, cyclic_type)
        pm = case / "constant" / region / "polyMesh"
        pm.mkdir(parents=True, exist_ok=True)
        loc = f"constant/{region}/polyMesh"
        fw.write_points(pm / "points", R["points"], loc, fmt)
        fw.write_faces(pm / "faces", R["faces"], loc, fmt)
        note = (f"nPoints:{R['npoints']}  nCells:{R['ncell']}  "
                f"nFaces:{R['nfaces']}  nInternalFaces:{R['n_internal']}")
        fw.write_labellist(pm / "owner", R["owner"], "owner", loc, note=note, fmt=fmt)
        fw.write_labellist(pm / "neighbour", R["neighbour"], "neighbour", loc,
                           note=note, fmt=fmt)
        fw.write_boundary(pm / "boundary", R["patches"], loc)
        # patch-map.json 항목 (S1 스키마)
        for rec in R["patches"]:
            ptype = {"patch": "inlet", "wall": "wall", "mappedWall": "interface",
                     "cyclic": "wall", "cyclicAMI": "wall"}[rec["type"]]
            # inlet/outlet 구분: 이름으로
            if rec["name"].endswith("_inlet"):
                ptype = "inlet"
            elif rec["name"].endswith("_outlet"):
                ptype = "outlet"
            elif rec["type"] == "mappedWall":
                ptype = "interface"
            elif rec["type"] == "cyclic":
                ptype = "wall"
            else:
                ptype = "wall"
            geom = _geom_ref(rec["name"])
            patch_map.append({"geomRef": geom, "patchName": rec["name"],
                              "patchType": ptype, "region": region,
                              "foamType": rec["type"]})
        R.pop("points"); R.pop("faces"); R.pop("owner"); R.pop("neighbour")
        region_reports[region] = R

    fw.write_region_properties(
        case / "constant" / "regionProperties",
        fluids=[r for r in ("hot", "cold") if present[r]],
        solids=[r for r in ("solid",) if present[r]])
    (case / "patch-map.json").write_text(
        json.dumps(patch_map, indent=2, ensure_ascii=False) + "\n")

    # 결정론 해시 (모든 산출 바이트)
    sha = _hash_case(case, present, fmt)
    report = {
        "input": str(vti_path), "case": str(case), "format": fmt,
        "dims": [nx, ny, nz], "spacing_mm": list(vol.spacing),
        "origin_mm": list(vol.origin),
        "label_histogram": {LABEL_NAME[i]: int(hist[i]) for i in range(4)},
        "cyclic_axes": sorted(cyclic), "cyclic_check": cyc_report,
        "regions": region_reports,
        "cell_count_check": {
            "per_region": {r: region_reports[r]["ncell"] for r in region_reports},
            "label_counts": {r: int(hist[rv]) for r, rv in REGIONS if present[r]},
            "bit_exact": all(region_reports[r]["ncell"] == int(hist[rv])
                             for r, rv in REGIONS if present[r]),
        },
        "artifact_sha256": sha,
        "wall_time_s": round(time.time() - t0, 3),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }
    (case / "convert-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


def _geom_ref(patch_name: str) -> str:
    """패치명 → geomRef (schema 패턴 ^[a-z][a-z0-9]*(\\.[a-z][a-z0-9_]*)*$)."""
    m = {
        "hot_inlet": "port.hot.inlet", "hot_outlet": "port.hot.outlet",
        "hot_wall": "wall.hot", "cold_inlet": "port.cold.inlet",
        "cold_outlet": "port.cold.outlet", "cold_wall": "wall.cold",
        "solid_external": "wall.solid.external",
        "hot_to_solid": "interface.hot_solid", "solid_to_hot": "interface.hot_solid",
        "cold_to_solid": "interface.cold_solid", "solid_to_cold": "interface.cold_solid",
    }
    if patch_name in m:
        return m[patch_name]
    # cyclic: <region>_<axis><side>
    return "cyclic." + patch_name.replace("_", ".", 1)


def _hash_case(case: Path, present: dict, fmt: str) -> dict:
    out = {}
    for region, ok in present.items():
        if not ok:
            continue
        h = hashlib.sha256()
        pm = case / "constant" / region / "polyMesh"
        for fn in ("points", "faces", "owner", "neighbour", "boundary"):
            h.update((case / "constant" / region / "polyMesh" / fn).read_bytes())
        out[region] = h.hexdigest()
    hb = hashlib.sha256((case / "constant" / "regionProperties").read_bytes())
    out["regionProperties"] = hb.hexdigest()
    hp = hashlib.sha256((case / "patch-map.json").read_bytes())
    out["patch-map.json"] = hp.hexdigest()
    combined = hashlib.sha256()
    for k in sorted(out):
        combined.update(k.encode()); combined.update(out[k].encode())
    out["combined"] = combined.hexdigest()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vti", help="labels.vti 경로 (UInt8 D-M0-2)")
    ap.add_argument("case", help="출력 케이스 디렉터리")
    ap.add_argument("--cyclic", default="",
                    help="주기 축 콤마구분 (y,z 만 — x는 유동축). 예: --cyclic y,z")
    ap.add_argument("--cyclic-type", default="cyclic", choices=["cyclic", "cyclicAMI"],
                    help="cyclic(conformal seam) | cyclicAMI(비순응 라벨 대조용)")
    ap.add_argument("--format", default="binary", choices=["binary", "ascii"])
    args = ap.parse_args()
    cyclic = frozenset(a.strip() for a in args.cyclic.split(",") if a.strip())
    report = convert(args.vti, args.case, cyclic, args.format, args.cyclic_type)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    ok = report["cell_count_check"]["bit_exact"]
    print(f"\n[labels_to_foam] cells bit-exact={ok} "
          f"time={report['wall_time_s']}s rss={report['peak_rss_mb']}MB "
          f"hash={report['artifact_sha256']['combined'][:16]}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

