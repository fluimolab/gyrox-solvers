"""Product transcription of the canonical Phase C window judge.

Source: ../gyrox/m0/m04/runs/phaseC/window_analysis_C.py
        @ ef610b9ecb4e7114d42c56f07584a0549f94a29c
Instantaneous span source: ../gyrox/m0/m01/evaluate_convergence.py
        @ ba130aca02c8dfe37f4e4a3e20c8a1a8a521ee7d
"""
from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class JudgeResult:
    exit_code: int
    outcome: str
    converged: bool
    window_mean_converged: bool
    dp_hot_window_mean_converged: bool
    axes: dict[str, Any]
    report_values: dict[str, float | int | None]


ALIASES = {
    "dpHotPa": "monitor_dpHotPa",
    "dpColdPa": "monitor_dpColdPa",
    "qHotW": "monitor_qHotSignedW",
    "qColdW": "monitor_qColdSignedW",
    "tOutHotK": "monitor_tOutHotK",
    "tOutColdK": "monitor_tOutColdK",
    "energyBalancePct": "monitor_energyBalancePct",
    "massHotPct": "monitor_mdotImbalanceHotPct",
    "massColdPct": "monitor_mdotImbalanceColdPct",
    "mdotHotKgS": "monitor_mdotHotKgS",
    "mdotColdKgS": "monitor_mdotColdKgS",
}


def _number(row: dict[str, Any], key: str) -> float | None:
    raw = row.get(key, row.get(ALIASES.get(key, "")))
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _series(rows: list[dict[str, Any]], key: str, *, absolute: bool = False) -> list[float]:
    values = [_number(row, key) for row in rows]
    return [abs(value) if absolute else value for value in values if value is not None]


def _q_series(rows: list[dict[str, Any]]) -> list[float]:
    values = []
    for row in rows:
        hot, cold = _number(row, "qHotW"), _number(row, "qColdW")
        if hot is not None and cold is not None:
            values.append(0.5 * (abs(hot) + abs(cold)))
    return values


def _wm_stats(values: list[float], window: int, *, kelvin: bool = False) -> dict[str, float] | None:
    finite = [value for value in values if math.isfinite(value)]
    if len(finite) < 2 * window:
        return None
    current = finite[-window:]
    window_mean = statistics.fmean(current)
    running = [statistics.fmean(finite[index - window:index]) for index in range(window, len(finite) + 1)]
    tail = running[-window:]
    if kelvin:
        drift, span = abs(tail[-1] - tail[0]), max(tail) - min(tail)
        return {"windowMean": window_mean, "driftK": round(drift, 4), "spanK": round(span, 4)}
    denominator = abs(tail[-1])
    drift = abs(tail[-1] - tail[0]) / denominator * 100 if denominator else math.inf
    span = (max(tail) - min(tail)) / denominator * 100 if denominator else math.inf
    amplitude = (max(current) - min(current)) / abs(window_mean) * 100 if window_mean else 0.0
    inst_denominator = max((abs(value) for value in current), default=0.0)
    inst_span = (max(current) - min(current)) / max(inst_denominator, 1e-30) * 100
    return {
        "windowMean": window_mean,
        "driftPct": round(drift, 4),
        "spanPct": round(span, 4),
        "amplitudePct": round(amplitude, 4),
        "instSpanPct": inst_span,
    }


def _window_complete(rows: list[dict[str, Any]], window: int, minimum: int) -> bool:
    if len(rows) < window:
        return False
    iterations = [_number(row, "iter") for row in rows[-window:]]
    if any(value is None or not value.is_integer() for value in iterations):
        return False
    ints = [int(value) for value in iterations if value is not None]
    return ints[-1] >= minimum and len(set(ints)) == window and all(b - a == 1 for a, b in zip(ints, ints[1:]))


def _finite(rows: list[dict[str, Any]], keys: Iterable[str]) -> bool:
    for row in rows:
        for key in keys:
            value = _number(row, key)
            if value is not None and not math.isfinite(value):
                return False
    return True


SIGN_PREDICATES = {
    "dpHotPa": lambda value: value > 0,
    "dpColdPa": lambda value: value > 0,
    "monitor_mdotHotInSignedKgS": lambda value: value < 0,
    "monitor_mdotHotOutSignedKgS": lambda value: value > 0,
    "monitor_mdotColdInSignedKgS": lambda value: value < 0,
    "monitor_mdotColdOutSignedKgS": lambda value: value > 0,
    "qHotW": lambda value: value < 0,
    "qColdW": lambda value: value > 0,
}


def _sign_status(rows: list[dict[str, Any]], window: int, minimum: int) -> tuple[bool, bool]:
    """Return ``(complete, pass)`` for the required signed judgment window.

    Missing sign samples are evidence insufficiency, not a physics violation.  A
    sign violation is only actionable after every required series has all W
    samples in the same complete judgment window.
    """
    if not _window_complete(rows, window, minimum):
        return False, False
    for key, predicate in SIGN_PREDICATES.items():
        values = [_number(row, key) for row in rows[-window:]]
        if not all(value is not None and math.isfinite(value) for value in values):
            return False, False
        if not all(predicate(value) for value in values if value is not None):
            return True, False
    return True, True


def _residual_blowup(rows: list[dict[str, Any]], window: int, factor: float, minimum: int) -> bool:
    if not _window_complete(rows, window, minimum) or len(rows) < 2 * window:
        return False
    fields = sorted({key for row in rows for key in row if key.startswith("residual_")})
    for field in fields:
        previous = [_number(row, field) for row in rows[-2 * window:-window]]
        current = [_number(row, field) for row in rows[-window:]]
        baseline_values = [abs(value) for value in previous if value is not None and math.isfinite(value)]
        current_values = [abs(value) for value in current if value is not None and math.isfinite(value)]
        if baseline_values and current_values:
            baseline = max(statistics.median(baseline_values), 1e-300)
            if max(current_values) > baseline * factor:
                return True
    return False


def _empty_axis() -> dict[str, Any]:
    return {
        "drift": {"pass": False, "driftPct": 0.0},
        "span": {"pass": False, "spanPct": 0.0},
        "instSpanPct": 0.0,
        "amplitudePct": 0.0,
    }


def judge_rows(rows: list[dict[str, Any]], convergence: dict[str, Any]) -> JudgeResult:
    window = int(convergence["windowIters"])
    transient_start = int(len(rows) * float(convergence["transientFrac"]))
    judged_rows = rows[transient_start:]
    q_series = {
        "dpHotPa": _series(judged_rows, "dpHotPa"),
        "dpColdPa": _series(judged_rows, "dpColdPa"),
        "qW": _q_series(judged_rows),
    }
    all_finite = _finite(rows, (
        *SIGN_PREDICATES, "tOutHotK", "tOutColdK",
        "energyBalancePct", "massHotPct", "massColdPct",
    ))
    sign_complete, sign_ok = _sign_status(
        judged_rows, window, int(convergence["minIterationsForJudgment"]),
    )
    blowup = _residual_blowup(rows, window, float(convergence["residualBlowupFactor"]), int(convergence["minIterationsForJudgment"]))
    diverged = (
        (bool(convergence["finiteRequired"]) and not all_finite)
        or (bool(convergence["signSanity"]) and sign_complete and not sign_ok)
        or blowup
    )

    q_stats = {key: _wm_stats(values, window) for key, values in q_series.items()}
    t_hot = _wm_stats(_series(judged_rows, "tOutHotK"), window, kelvin=True)
    t_cold = _wm_stats(_series(judged_rows, "tOutColdK"), window, kelvin=True)
    eb_stats = _wm_stats(_series(judged_rows, "energyBalancePct", absolute=True), window)
    mass_hot_stats = _wm_stats(_series(judged_rows, "massHotPct", absolute=True), window)
    mass_cold_stats = _wm_stats(_series(judged_rows, "massColdPct", absolute=True), window)
    eb_mean = eb_stats["windowMean"] if eb_stats is not None else None
    mass_hot = mass_hot_stats["windowMean"] if mass_hot_stats is not None else None
    mass_cold = mass_cold_stats["windowMean"] if mass_cold_stats is not None else None
    insufficient = (
        any(value is None for value in q_stats.values())
        or t_hot is None
        or t_cold is None
        or None in (eb_mean, mass_hot, mass_cold)
        or (bool(convergence["signSanity"]) and not sign_complete)
    )

    per_qoi: dict[str, Any] = {}
    mean_pass: dict[str, bool] = {}
    inst_pass: dict[str, bool] = {}
    for key in convergence["judgedQoI"]:
        stats = q_stats.get(key)
        if stats is None:
            per_qoi[key] = _empty_axis()
            mean_pass[key] = inst_pass[key] = False
            continue
        drift_pass = stats["driftPct"] < float(convergence["windowMeanDriftPct"])
        span_pass = stats["spanPct"] < float(convergence["windowMeanSpanPct"])
        per_qoi[key] = {
            "drift": {"pass": drift_pass, "driftPct": stats["driftPct"]},
            "span": {"pass": span_pass, "spanPct": stats["spanPct"]},
            "instSpanPct": stats["instSpanPct"],
            "amplitudePct": stats["amplitudePct"],
        }
        mean_pass[key] = drift_pass and span_pass
        inst_pass[key] = stats["instSpanPct"] < float(convergence["instSpanPct"])

    t_hot_axis = {"driftK": 0.0, "spanK": 0.0, "pass": False} if t_hot is None else {
        "driftK": t_hot["driftK"], "spanK": t_hot["spanK"],
        "pass": t_hot["driftK"] < convergence["tOutDriftK"] and t_hot["spanK"] < convergence["tOutSpanK"],
    }
    t_cold_axis = {"driftK": 0.0, "spanK": 0.0, "pass": False} if t_cold is None else {
        "driftK": t_cold["driftK"], "spanK": t_cold["spanK"],
        "pass": t_cold["driftK"] < convergence["tOutDriftK"] and t_cold["spanK"] < convergence["tOutSpanK"],
    }
    eb_value = eb_mean if eb_mean is not None else 0.0
    eb_pass = eb_mean is not None and eb_mean < convergence["ebMaxPct"]
    eb_flag = "owner_escalate" if eb_value >= convergence["ebMaxPct"] else "warn" if eb_value >= convergence["ebWarnPct"] else "ok"
    mass_pass = mass_hot is not None and mass_cold is not None and mass_hot < convergence["massImbalanceMaxPct"] and mass_cold < convergence["massImbalanceMaxPct"]
    axes = {
        "perQoI": per_qoi,
        "tOutHot": t_hot_axis,
        "tOutCold": t_cold_axis,
        "eb": {"windowMeanPct": eb_value, "flag": eb_flag, "pass": eb_pass},
        "mass": {"hotPct": mass_hot or 0.0, "coldPct": mass_cold or 0.0, "pass": mass_pass},
        "sign": {"pass": sign_ok},
        "finite": {"pass": all_finite},
        "insufficient": insufficient,
    }
    common_valid = eb_pass and mass_pass and sign_ok and all_finite and not insufficient
    window_mean_converged = all(mean_pass.values()) and t_hot_axis["pass"] and t_cold_axis["pass"] and common_valid
    dp_hot_window_mean_converged = mean_pass.get("dpHotPa", False) and common_valid
    converged = window_mean_converged and all(inst_pass.values())

    reporting: dict[str, float | int | None] = {key: None for key in (
        "dpHotPa", "dpColdPa", "mdotHotKgS", "mdotColdKgS", "fFactor", "qW", "qHotW", "qColdW",
        "effectiveness", "ntu", "uaWK", "jFactor", "tOutHotK", "tOutColdK", "energyBalancePct",
    )}
    for key, stats in q_stats.items():
        reporting[key] = stats["windowMean"] if stats else None
    for key in ("qHotW", "qColdW", "mdotHotKgS", "mdotColdKgS", "tOutHotK", "tOutColdK"):
        values = _series(judged_rows, key)
        reporting[key] = statistics.fmean(values[-window:]) if len(values) >= window else None
    reporting["energyBalancePct"] = eb_mean
    reporting["iterations"] = int(_number(rows[-1], "iter")) if rows and _number(rows[-1], "iter") is not None else None

    if diverged:
        return JudgeResult(20, "PHYSICS_DIVERGED", False, False, False, axes, reporting)
    if converged:
        return JudgeResult(0, "SUCCEEDED", True, True, dp_hot_window_mean_converged, axes, reporting)
    return JudgeResult(10, "SUCCEEDED_WITH_WARNINGS", False, window_mean_converged, dp_hot_window_mean_converged, axes, reporting)


def judge_csv(path: Path, convergence: dict[str, Any], *, max_iter: int | None = None) -> JudgeResult:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if max_iter is not None:
        rows = [row for row in rows if (_number(row, "iter") or math.inf) <= max_iter]
    return judge_rows(rows, convergence)
