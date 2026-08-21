# Source dependency: ../gyrox/m0/mesh-paths/common/vti_io.py @ ba130aca02c8dfe37f4e4a3e20c8a1a8a521ee7d
"""VTI(ImageData) 최소 리더/라이터 — M0-2 V1-V5 게이트용.

계약 (fixture gyroid-core-30 / M0-3 라벨 라이터 참조 규격):
- UInt8 라벨, appended raw 비압축, header_type UInt64 → 슬라이스 스트리밍(np.memmap)
- 복셀 수 산정은 **DataArray 소속으로 판별** (웨이브 2b 실측 — 실물 fixture 정합):
    CellData  + WholeExtent 0..N  → 축당 N 셀   (이 모듈의 라이터 규격)
    PointData + WholeExtent 0..N-1 → 축당 N 샘플 (M0-3 geometry job 실제 산출 —
      복셀=포인트 관례. 2026-07-11 실측: 이걸 셀 관례로 읽으면 299³ 전단(shear)이
      생겨 V1/V2가 거짓 FAIL — 가짜 누설 20만 쌍)
- 두 관례 모두 appended 헤더의 바이트 수와 dims 곱을 대조해 불일치 시 즉시 오류
- base64/ascii/압축은 폴백으로만 읽기 지원(전체 로드 — 스트리밍 불가 경고)
"""
from __future__ import annotations

import base64
import re
import sys
import zlib
from dataclasses import dataclass

import numpy as np

_DTYPES = {
    "UInt8": np.uint8, "Int8": np.int8,
    "UInt16": np.uint16, "Int16": np.int16,
    "UInt32": np.uint32, "Int32": np.int32,
    "UInt64": np.uint64, "Int64": np.int64,
    "Float32": np.float32, "Float64": np.float64,
}


@dataclass
class VtiVolume:
    """z-슬라이스 스트리밍 핸들. data[z] -> (ny, nx) 배열."""
    dims: tuple[int, int, int]          # (nx, ny, nz) — 셀 수
    spacing: tuple[float, float, float]
    origin: tuple[float, float, float]
    array_name: str
    _data: np.ndarray                    # shape (nz, ny, nx), memmap 또는 in-memory
    streaming: bool

    def slice_z(self, z: int) -> np.ndarray:
        return self._data[z]

    def iter_slices(self):
        for z in range(self.dims[2]):
            yield z, self._data[z]


def _attr(tag: str, name: str, default=None):
    m = re.search(rf'{name}="([^"]*)"', tag)
    return m.group(1) if m else default


def read_vti_uint8(path: str) -> VtiVolume:
    with open(path, "rb") as f:
        head = f.read(64 * 1024)

    # WholeExtent → 복셀 수. 관례 판별: DataArray가 CellData 소속이면 축당 N 셀
    # (extent=포인트 인덱스 0..N), PointData 소속이면 축당 N 샘플(0..N-1) —
    # M0-3 geometry job 산출 규격 (모듈 docstring 참조).
    img_tag = re.search(rb"<ImageData[^>]*>", head)
    if not img_tag:
        raise ValueError(f"{path}: <ImageData> 태그 없음")
    tag = img_tag.group(0).decode()
    ext = [int(v) for v in _attr(tag, "WholeExtent").split()]
    spacing = tuple(float(v) for v in _attr(tag, "Spacing", "1 1 1").split())
    origin = tuple(float(v) for v in _attr(tag, "Origin", "0 0 0").split())

    vf_tag = re.search(rb"<VTKFile[^>]*>", head).group(0).decode()
    header_type = _attr(vf_tag, "header_type", "UInt32")
    compressor = _attr(vf_tag, "compressor")
    hdr_dtype = np.uint64 if header_type == "UInt64" else np.uint32

    da_tag = re.search(rb'<DataArray[^>]*Name="[^"]*"[^>]*>', head)
    if da_tag is None:
        raise ValueError(f"{path}: DataArray 없음")
    da = da_tag.group(0).decode()
    name = _attr(da, "Name", "labels")
    dtype = _DTYPES[_attr(da, "type", "UInt8")]
    fmt = _attr(da, "format", "appended")

    # PointData/CellData 소속 판별 — DataArray 태그 위치가 어느 블록 안인가
    pd = re.search(rb"<PointData[^>]*>.*?</PointData>", head, re.S)
    point_data = bool(pd and pd.start() < da_tag.start() < pd.end())
    if point_data:
        nx, ny, nz = ext[1] - ext[0] + 1, ext[3] - ext[2] + 1, ext[5] - ext[4] + 1
    else:
        nx, ny, nz = ext[1] - ext[0], ext[3] - ext[2], ext[5] - ext[4]
    ncell = nx * ny * nz

    if fmt == "appended":
        m = re.search(rb'<AppendedData\s+encoding="(\w+)"\s*>', head)
        enc = m.group(1).decode()
        us = head.find(b"_", m.end())
        data_start = us + 1 + int(_attr(da, "offset", "0"))
        if enc == "raw" and compressor is None:
            # 가드: appended 헤더의 바이트 수 = dims 곱 (관례 오판 시 즉시 실패 —
            # 웨이브 2b에서 침묵 전단으로 V-게이트가 거짓 FAIL했던 사고 재발 방지)
            with open(path, "rb") as f:
                f.seek(data_start)
                nbytes = int(np.frombuffer(f.read(np.dtype(hdr_dtype).itemsize),
                                           dtype=hdr_dtype)[0])
            if nbytes != ncell * np.dtype(dtype).itemsize:
                raise ValueError(
                    f"{path}: appended 바이트 {nbytes} ≠ dims 곱 "
                    f"{ncell}×{np.dtype(dtype).itemsize} "
                    f"(extent 관례 오판? pointData={point_data}, dims={(nx, ny, nz)})")
            arr = np.memmap(path, dtype=dtype, mode="r",
                            offset=data_start + np.dtype(hdr_dtype).itemsize,
                            shape=(nz, ny, nx))
            return VtiVolume((nx, ny, nz), spacing, origin, name, arr, True)
        # 폴백: base64 / 압축 — 전체 로드
        print(f"[vti_io] 경고: {path} encoding={enc} compressor={compressor} — "
              f"스트리밍 불가, 전체 로드 폴백", file=sys.stderr)
        with open(path, "rb") as f:
            blob = f.read()[data_start:]
        if enc == "base64":
            if compressor is None:
                raw = base64.b64decode(blob)[np.dtype(hdr_dtype).itemsize:]
            else:
                hsz = np.dtype(hdr_dtype).itemsize
                hdr = np.frombuffer(base64.b64decode(blob[: 4 * ((3 * hsz + 2) // 3 + 1)]),
                                    dtype=hdr_dtype, count=3)
                nblk = int(hdr[0])
                hdr_b64_len = 4 * (((3 + nblk) * hsz + 2) // 3)
                full = np.frombuffer(base64.b64decode(blob[:hdr_b64_len]), dtype=hdr_dtype)
                payload = base64.b64decode(blob[hdr_b64_len:])
                raw = b"".join(zlib.decompress(payload) for _ in range(1)) if nblk == 1 \
                    else _decompress_blocks(full, payload)
        else:
            raise ValueError(f"미지원 인코딩: {enc}")
        arr = np.frombuffer(raw, dtype=dtype, count=ncell).reshape(nz, ny, nx)
        return VtiVolume((nx, ny, nz), spacing, origin, name, arr, False)

    if fmt == "ascii":
        body = re.search(rb"<DataArray[^>]*>(.*?)</DataArray>", open(path, "rb").read(),
                         re.S).group(1)
        arr = np.fromstring(body, dtype=dtype, sep=" ") if False else \
            np.array(body.split(), dtype=dtype)
        return VtiVolume((nx, ny, nz), spacing, origin, name,
                         arr.reshape(nz, ny, nx), False)
    raise ValueError(f"미지원 format: {fmt}")


def _decompress_blocks(hdr: np.ndarray, payload: bytes) -> bytes:
    nblk = int(hdr[0])
    sizes = hdr[3: 3 + nblk].astype(int)
    out, pos = [], 0
    for s in sizes:
        out.append(zlib.decompress(payload[pos: pos + s]))
        pos += s
    return b"".join(out)


def write_vti_uint8(path: str, data: np.ndarray, spacing_mm: float,
                    origin=(0.0, 0.0, 0.0), name: str = "labels") -> None:
    """data shape (nz, ny, nx) uint8 → CellData, appended raw 비압축 (스트리밍 계약 규격)."""
    nz, ny, nx = data.shape
    s = spacing_mm
    nbytes = data.size
    header = (
        '<?xml version="1.0"?>\n'
        '<VTKFile type="ImageData" version="1.0" byte_order="LittleEndian" '
        'header_type="UInt64">\n'
        f'  <ImageData WholeExtent="0 {nx} 0 {ny} 0 {nz}" '
        f'Origin="{origin[0]} {origin[1]} {origin[2]}" Spacing="{s} {s} {s}">\n'
        f'    <Piece Extent="0 {nx} 0 {ny} 0 {nz}">\n'
        '      <PointData/>\n'
        f'      <CellData Scalars="{name}">\n'
        f'        <DataArray type="UInt8" Name="{name}" format="appended" offset="0"/>\n'
        '      </CellData>\n'
        '    </Piece>\n'
        '  </ImageData>\n'
        '  <AppendedData encoding="raw">\n_'
    )
    footer = "\n  </AppendedData>\n</VTKFile>\n"
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(np.uint64(nbytes).tobytes())
        for z in range(nz):  # 슬라이스 단위 기록 — 대용량에서도 O(slice)
            f.write(np.ascontiguousarray(data[z], dtype=np.uint8).tobytes())
        f.write(footer.encode())

