# Source: ../gyrox/m0/results/extract/common.py @ ba130aca02c8dfe37f4e4a3e20c8a1a8a521ee7d
"""공통 유틸 — patch-map 로드, OpenFOAM postProcessing .dat 파싱, 물성/로그 파싱."""
from __future__ import annotations

import json
import re
from pathlib import Path


def load_patch_map(case_dir: Path) -> dict:
    pm_path = case_dir / "patch-map.json"
    if not pm_path.exists():
        raise FileNotFoundError(
            f"{pm_path} 없음 — 케이스에 patch-map.json 필요 (README 입력 계약)")
    return json.loads(pm_path.read_text())


def _fo_dirs(case_dir: Path, fo_name: str) -> list[Path]:
    """postProcessing/<fo>/<time>/ 디렉터리들(시간 오름차순).

    region 지정 FO는 postProcessing/<fo>/<time>/ 또는
    postProcessing/<region>/<fo>/<time>/ 에 놓일 수 있어 둘 다 탐색한다.
    """
    hits: list[Path] = []
    pp = case_dir / "postProcessing"
    for base in [pp / fo_name, *pp.glob(f"*/{fo_name}")]:
        if base.is_dir():
            for td in base.iterdir():
                if td.is_dir() and re.fullmatch(r"[0-9.eE+-]+", td.name):
                    hits.append(td)
    return sorted(hits, key=lambda p: float(p.name))


def read_dat(case_dir: Path, fo_name: str, file_glob: str = "*.dat"):
    """FO 출력 .dat -> (header_cols, rows). 시간 디렉터리 여러 개면 이어붙인다.

    OpenFOAM .dat: '#' 주석 헤더(마지막 헤더 행 = 컬럼명), 탭/공백 구분.
    비수치 컬럼(패치명 등)은 문자열로 유지.

    동일 (time[, patch]) 중복 행은 첫 값 유지 — 솔브 시점 기록(시작시간 디렉터리)이
    -postProcess 소급 재계산(저장 필드 writePrecision 손실)보다 우선
    (실측: 소급 areaAverage(p)가 6자리 양자화로 dp를 0으로 뭉갬).
    """
    cols: list[str] | None = None
    rows: list[list] = []
    seen: set = set()
    for td in _fo_dirs(case_dir, fo_name):
        for f in sorted(td.glob(file_glob)):
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                if line.startswith("#"):
                    parts = line.lstrip("#").split()
                    if parts:
                        cols = parts
                    continue
                vals = []
                for tok in line.split():
                    try:
                        vals.append(float(tok))
                    except ValueError:
                        vals.append(tok)
                key = (vals[0], vals[1] if len(vals) > 1 and isinstance(vals[1], str) else None)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(vals)
    if cols is None and not rows:
        raise FileNotFoundError(f"FO 출력 없음: {fo_name} (postProcessing/ 아래)")
    return cols, rows


def last_value(case_dir: Path, fo_name: str, col_index: int = 1) -> float:
    _, rows = read_dat(case_dir, fo_name)
    return float(rows[-1][col_index])


def parse_foam_scalar(text: str, key: str) -> float | None:
    """OpenFOAM 딕셔너리 텍스트에서 스칼라 항목 1개 추출 (중첩 무시, 첫 매치)."""
    m = re.search(rf"\b{re.escape(key)}\s+([0-9eE.+-]+)\s*;", text)
    return float(m.group(1)) if m else None


def fluid_cp(case_dir: Path, region: str) -> float:
    """thermophysicalProperties에서 Cp (hConst 상수 근사 전제 — README 입력 계약)."""
    tp = (case_dir / "constant" / region / "thermophysicalProperties").read_text()
    cp = parse_foam_scalar(tp, "Cp")
    if cp is None:
        raise ValueError(f"{region}: Cp 파싱 실패 (hConst 상수 물성만 지원)")
    return cp


def region_cell_count(case_dir: Path, region: str) -> int:
    """polyMesh/owner 헤더의 'nCells:<n>' 노트 파싱."""
    owner = case_dir / "constant" / region / "polyMesh" / "owner"
    head = owner.read_text(errors="ignore")[:2000]
    m = re.search(r"nCells:\s*(\d+)", head)
    if not m:
        raise ValueError(f"{owner}: nCells 노트 파싱 실패")
    return int(m.group(1))


def _solver_log_path(case_dir: Path,
                     app: str = "chtMultiRegionSimpleFoam") -> Path | None:
    """solver log 위치 — make-dev-case.sh(log.<app>)와 mesh-paths 러너
    (logs/<app>.log) 두 배치 모두 지원 (M0-1 caseA 실측 배치)."""
    for cand in (case_dir / f"log.{app}", case_dir / "logs" / f"{app}.log",
                 case_dir / "logs" / "cht.log"):
        if cand.exists():
            return cand
    return None


def parse_solver_log(case_dir: Path, log_name: str = "log.chtMultiRegionSimpleFoam"):
    """solver log -> (iterations, wallClockS, convergedByLog).

    M0 대체 소스 (러너/S2 envelope 부재 — 브리프 openQuestion 승인 전제).
    """
    log = case_dir / log_name
    if not log.exists():
        log = _solver_log_path(case_dir)
    if log is None or not log.exists():
        return None, None, None
    iterations = None
    wall_clock = None
    converged_msg = False
    for line in log.read_text(errors="ignore").splitlines():
        if line.startswith("Time = "):
            try:
                iterations = int(float(line.split("=", 1)[1].strip().rstrip("s")))
            except ValueError:
                pass
        elif "ClockTime" in line:
            m = re.search(r"ClockTime\s*=\s*([0-9.]+)\s*s", line)
            if m:
                wall_clock = float(m.group(1))
        elif "solution converged" in line:
            converged_msg = True
    return iterations, wall_clock, converged_msg


_WHF_LOG_CACHE: dict = {}


def parse_whf_log(case_dir: Path, fo_name: str) -> dict:
    """솔버 로그의 wallHeatFlux 모니터 행 파싱 -> {iter: {patch: integral}}.

    폴백 소스 (caseA L0 실측 함정): 2512 병렬 솔브에서 wallHeatFlux FO가
    writeControl timeStep + writeInterval 50 지정에도 dat 데이터 행을 전혀
    안 남김(헤더만). log yes 덕에 매 iter 'wallHeatFlux <fo> execute:' +
    'min/max/integ(<patch>) = min, max, integ' 행은 로그에 있어 여기서 복원.
    소급 -postProcess는 최종 시각 1행만 재구성 가능 (README 커버리지 표).
    """
    key = str(case_dir.resolve())
    if key not in _WHF_LOG_CACHE:
        log = _solver_log_path(case_dir)
        series: dict[str, dict[int, dict[str, float]]] = {}
        if log is not None:
            t = None
            cur_fo = None
            pat_time = re.compile(r"^Time = (\d+)")
            pat_fo = re.compile(r"^wallHeatFlux (\w+) (?:execute|write):")
            pat_integ = re.compile(
                r"min/max/integ\(([\w.]+)\) = [^,]+, [^,]+, ([-\d.eE+]+)")
            for line in log.read_text(errors="ignore").splitlines():
                m = pat_time.match(line)
                if m:
                    t = int(m.group(1))
                    continue
                m = pat_fo.match(line)
                if m:
                    cur_fo = m.group(1)
                    continue
                m = pat_integ.search(line)
                if m and t is not None and cur_fo is not None:
                    series.setdefault(cur_fo, {}).setdefault(
                        t, {})[m.group(1)] = float(m.group(2))
        _WHF_LOG_CACHE[key] = series
    return _WHF_LOG_CACHE[key].get(fo_name, {})


def solver_exit_code(case_dir: Path) -> int | None:
    f = case_dir / "solver.exitcode"
    if f.exists():
        try:
            return int(f.read_text().strip())
        except ValueError:
            return None
    return None
