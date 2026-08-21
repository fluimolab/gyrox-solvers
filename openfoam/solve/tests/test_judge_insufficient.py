# ADR-006 D8-2: QoI identifiers below are contract data, not new verification claims.
from __future__ import annotations

import pytest

from openfoam.solve.judge import judge_rows
from openfoam.solve.tests.conftest import stable_rows


@pytest.mark.parametrize("_case", [None], ids=["[SR-06]"])
def test_insufficient_fails_closed(_case, convergence):
    result = judge_rows(stable_rows(399), convergence)
    assert result.exit_code == 10
    assert result.axes["insufficient"] is True
    assert result.converged is result.window_mean_converged is result.dp_hot_window_mean_converged is False


@pytest.mark.parametrize("missing", [
    ("dpHotPa",), ("dpColdPa",), ("qHotW", "qColdW"),
    ("tOutHotK",), ("tOutColdK",), ("energyBalancePct",),
    ("massHotPct",), ("massColdPct",),
])
def test_each_judgment_series_requires_2w(missing, convergence):
    rows = stable_rows(500)  # transient cut leaves exactly 2W rows
    for key in missing:
        rows[100].pop(key)
    result = judge_rows(rows, convergence)
    assert (result.exit_code, result.axes["insufficient"]) == (10, True)


@pytest.mark.parametrize("key", ["energyBalancePct", "massHotPct", "massColdPct"])
def test_auxiliary_series_with_only_w_samples_is_insufficient(key, convergence):
    rows = stable_rows(500)
    for row in rows[100:300]:
        row.pop(key)
    result = judge_rows(rows, convergence)
    assert (result.exit_code, result.axes["insufficient"]) == (10, True)


SIGN_KEYS = (
    "dpHotPa", "dpColdPa",
    "monitor_mdotHotInSignedKgS", "monitor_mdotHotOutSignedKgS",
    "monitor_mdotColdInSignedKgS", "monitor_mdotColdOutSignedKgS",
    "qHotW", "qColdW",
)


@pytest.mark.parametrize("missing_key", SIGN_KEYS)
def test_each_required_sign_series_needs_every_window_sample(missing_key, convergence):
    rows = stable_rows()
    rows[-1].pop(missing_key)
    result = judge_rows(rows, convergence)
    assert (result.exit_code, result.outcome) == (10, "SUCCEEDED_WITH_WARNINGS")
    assert result.axes["insufficient"] is True
    assert result.axes["sign"]["pass"] is False


def test_missing_sign_window_is_insufficient_not_diverged(convergence):
    all_missing = stable_rows()
    for row in all_missing:
        for key in SIGN_KEYS:
            row.pop(key)
    one_missing = stable_rows()
    one_missing[-1].pop("monitor_mdotHotInSignedKgS")
    for rows in (all_missing, one_missing):
        result = judge_rows(rows, convergence)
        assert (result.exit_code, result.outcome) == (10, "SUCCEEDED_WITH_WARNINGS")
        assert result.axes["insufficient"] is True
        assert result.axes["sign"]["pass"] is False


def test_pretransient_samples_do_not_fill_a_posttransient_gap(convergence):
    rows = stable_rows(500)
    rows[100].pop("energyBalancePct")
    result = judge_rows(rows, convergence)
    assert (result.exit_code, result.axes["insufficient"]) == (10, True)
