# Source: ../gyrox/m0/results/extract/timeseries.py @ ba130aca02c8dfe37f4e4a3e20c8a1a8a521ee7d
"""timeseries.csv — 영역×필드 잔차(solverInfo) + 모니터(surfaceFieldValue/wallHeatFlux)
iter 기준 병합 + matplotlib 수렴 곡선 스모크 플롯.

M1 progress envelope(S2: residuals{U,p,T}+energyBalancePct+iter)의 동등 데이터를
OpenFOAM function object 출력에서 재구성한다 (러너 부재 — M0 대체).
"""
from __future__ import annotations

import csv
from pathlib import Path

from .common import fluid_cp, parse_whf_log, read_dat


def _residual_series(case_dir: Path, fo_name: str, fields: list[str]) -> dict:
    """solverInfo.dat -> {field: {iter: initial_residual}} (U는 성분 최대값)."""
    cols, rows = read_dat(case_dir, fo_name, "solverInfo.dat")
    out: dict[str, dict[int, float]] = {f: {} for f in fields}
    if cols is None:
        return out  # 소급 -postProcess: solverInfo가 시간 값만 기록 (잔차 소급 불가 실측)
    idx = {c: i for i, c in enumerate(cols)}
    for r in rows:
        it = int(float(r[0]))
        for f in fields:
            if f == "U":
                comps = [idx.get(c) for c in ("Ux_initial", "Uy_initial", "Uz_initial")]
                vals = [float(r[i]) for i in comps if i is not None and i < len(r)]
                if vals:
                    out[f][it] = max(vals)
            else:
                i = idx.get(f"{f}_initial")
                if i is not None and i < len(r):
                    out[f][it] = float(r[i])
    return out


def _monitor_series(case_dir: Path, fo_name: str, col: int = 1,
                    scale: float = 1.0, absolute: bool = False) -> dict[int, float]:
    _, rows = read_dat(case_dir, fo_name, "surfaceFieldValue.dat")
    out = {}
    for r in rows:
        v = float(r[col]) * scale
        out[int(float(r[0]))] = abs(v) if absolute else v
    return out


def _whf_series(case_dir: Path, fo_name: str) -> dict[int, float]:
    """wallHeatFlux.dat -> {iter: signed Σ patch integral}.

    dat 데이터 행이 2개 미만이면 솔버 로그 모니터 행에서 복원
    (실측: 2512 병렬 솔브가 dat에 헤더만 남김 — common.parse_whf_log)."""
    try:
        _, rows = read_dat(case_dir, fo_name, "wallHeatFlux.dat")
    except FileNotFoundError:
        rows = []
    acc: dict[int, float] = {}
    for r in rows:
        it = int(float(r[0]))
        acc[it] = acc.get(it, 0.0) + float(r[4])
    if len(acc) < 2:
        log_series = parse_whf_log(case_dir, fo_name)
        if log_series:
            acc = {it: sum(patches.values())
                   for it, patches in log_series.items()}
    return acc


def build_timeseries(case_dir: Path, patch_map: dict, out_csv: Path,
                     plot_png: Path | None = None) -> dict:
    roles = patch_map["roles"]
    fluid_fields = patch_map["residualFields"]["fluid"]
    solid_fields = patch_map["residualFields"]["solid"]

    columns: dict[str, dict[int, float]] = {}

    for role in ("hot", "cold"):
        region = roles[role]["region"]
        res = _residual_series(case_dir, f"solverInfo_{role}", fluid_fields)
        for f, series in res.items():
            columns[f"residual_{region}_{f}"] = series
    for solid in patch_map["solidRegions"]:
        res = _residual_series(case_dir, f"solverInfo_solid_{solid}", solid_fields)
        for f, series in res.items():
            columns[f"residual_{solid}_{f}"] = series

    for role in ("hot", "cold"):
        cap = role.capitalize()
        mdot_in_signed = _monitor_series(case_dir, f"mdot_{role}_in")
        mdot_out_signed = _monitor_series(case_dir, f"mdot_{role}_out")
        mdot_in = {it: abs(value) for it, value in mdot_in_signed.items()}
        mdot_out = {it: abs(value) for it, value in mdot_out_signed.items()}
        # 기존 input-only 열 이름은 호환성을 위해 유지한다. M0.1부터 out과
        # 질량 불균형을 함께 기록해 고정 유량 BC가 격자별로 같은 문제를 푸는지
        # 기계 판정할 수 있게 한다.
        columns[f"monitor_mdot{cap}KgS"] = mdot_in
        columns[f"monitor_mdot{cap}OutKgS"] = mdot_out
        columns[f"monitor_mdot{cap}InSignedKgS"] = mdot_in_signed
        columns[f"monitor_mdot{cap}OutSignedKgS"] = mdot_out_signed
        columns[f"monitor_mdotImbalance{cap}Pct"] = {
            # outward-positive convention: a conservative inlet/outlet pair
            # sums to zero. Unlike abs(|in|-|out|), this catches two outward
            # patches or a reversed inlet BC.
            it: (abs(mdot_in_signed[it] + mdot_out_signed[it])
                 / abs(mdot_in_signed[it]) * 100.0
                 if mdot_in_signed[it] else float("nan"))
            for it in mdot_in_signed if it in mdot_out_signed}
        p_in = _monitor_series(case_dir, f"p_{role}_in")
        p_out = _monitor_series(case_dir, f"p_{role}_out")
        columns[f"monitor_dp{role.capitalize()}Pa"] = {
            it: p_in[it] - p_out[it] for it in p_in if it in p_out}
        columns[f"monitor_tIn{cap}K"] = _monitor_series(
            case_dir, f"T_{role}_in")
        columns[f"monitor_tOut{role.capitalize()}K"] = _monitor_series(
            case_dir, f"T_{role}_out")
        q_signed = _whf_series(case_dir, f"whf_{role}")
        columns[f"monitor_q{cap}SignedW"] = q_signed
        columns[f"monitor_q{cap}W"] = {
            it: abs(value) for it, value in q_signed.items()}

        cp = fluid_cp(case_dir, region)
        t_in = columns[f"monitor_tIn{cap}K"]
        t_out = columns[f"monitor_tOut{cap}K"]
        q_enthalpy: dict[int, float] = {}
        q_closure: dict[int, float] = {}
        for it in set(mdot_in) & set(t_in) & set(t_out) & set(q_signed):
            delta_t = (t_in[it] - t_out[it] if role == "hot"
                       else t_out[it] - t_in[it])
            q_enthalpy[it] = mdot_in[it] * cp * delta_t
            scale = max(abs(q_signed[it]), abs(q_enthalpy[it]), 1e-30)
            q_closure[it] = (
                abs(abs(q_signed[it]) - q_enthalpy[it]) / scale * 100.0)
        columns[f"monitor_q{cap}EnthalpyW"] = q_enthalpy
        columns[f"monitor_q{cap}EnthalpyClosurePct"] = q_closure

    qh, qc = columns["monitor_qHotW"], columns["monitor_qColdW"]
    columns["monitor_energyBalancePct"] = {
        it: (abs(qh[it] - qc[it]) / ((qh[it] + qc[it]) / 2.0) * 100.0
             if (qh[it] + qc[it]) > 0 else float("nan"))
        for it in qh if it in qc}

    iters = sorted({it for series in columns.values() for it in series})
    names = list(columns)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["iter", *names])
        for it in iters:
            w.writerow([it, *[columns[n].get(it, "") for n in names]])

    if plot_png is not None:
        _smoke_plot(columns, iters, plot_png)

    return {"rows": len(iters), "columns": 1 + len(names)}


def final_max_residual(case_dir: Path, patch_map: dict) -> float:
    """유체 2영역 최종 iter의 initial residual 최대값 (converged 판정 입력)."""
    worst = 0.0
    for role in ("hot", "cold"):
        res = _residual_series(
            case_dir, f"solverInfo_{role}", patch_map["residualFields"]["fluid"])
        for series in res.values():
            if series:
                worst = max(worst, series[max(series)])
    return worst


def _smoke_plot(columns: dict, iters: list[int], plot_png: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for name, series in columns.items():
        if name.startswith("residual_"):
            xs = sorted(series)
            ax1.semilogy(xs, [series[x] for x in xs], label=name[len("residual_"):])
    ax1.set_ylabel("initial residual")
    ax1.legend(fontsize=6, ncol=3)
    ax1.grid(True, alpha=0.3)

    eb = columns.get("monitor_energyBalancePct", {})
    xs = sorted(eb)
    ax2.semilogy(xs, [max(eb[x], 1e-12) for x in xs], color="tab:red",
                 label="energyBalancePct")
    ax2.axhline(1.0, color="gray", ls="--", lw=0.8, label="1% gate")
    ax2.set_xlabel("iteration")
    ax2.set_ylabel("|Q_hot-Q_cold|/Q [%]")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    plot_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_png, dpi=110)
    plt.close(fig)

