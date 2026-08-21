from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest


TITLES = {
    "test_renderer_join": "[SR-01] 렌더러 조인(인렛 2항·아웃렛 상수)",
    "test_thermo_roundtrip": "[SR-02] 물성 왕복 원값 일치",
    "test_fingerprint": "[SR-03] 렌더 지문(코어 수 불변·relaxation 변경 감지)",
    "test_judge_golden": "[SR-04] judge 골든 = CL2 기록 재현",
    "test_judge_axes": "[SR-05] judge 3필드 축 분리",
    "test_judge_insufficient": "[SR-06] insufficient fail-closed",
    "test_divergence": "[SR-07] 발산 선행 3분기(exit 20)",
    "test_exit_summary": "[SR-08] exit 매핑·summary 이중 기록",
    "test_ckpt_produce": "[SR-09] checkpoint 생산(tar·9필드·ndjson)",
    "test_ckpt_resume": "[SR-10] 재개 3분기",
    "test_patchmap_validate": "[SR-11] patch-map 재검증 exit 30",
    "test_verification": "[SR-12] verification 보수 기본값",
    "test_policy_passthrough": "[SR-13] executorPolicy 전사·fallback 부재",
}


def pytest_collection_modifyitems(items):
    for item in items:
        title = TITLES.get(item.path.stem)
        if title and title.split(" ", 1)[0] in item.name:
            item._nodeid = f"{item.path.as_posix()}::{title}"
            item.name = title


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def gyrox_root(repo_root) -> Path:
    configured = os.environ.get("GYROX_SOURCE_ROOT")
    root = Path(configured) if configured else repo_root.parent / "gyrox"
    if not root.is_dir():
        pytest.fail(f"required Gyrox M0 source tree is absent: {root}", pytrace=False)
    return root


@pytest.fixture
def solve_document(repo_root):
    baseline = json.loads((repo_root / "contract/fixtures/hash/accept/A1-four-spec-baseline.json").read_text())
    document = copy.deepcopy(baseline["inputs"]["solve"])
    document["payload"]["materials"] = {
        "hot": {"rhoKgM3": 983.2, "cpJKgK": 4183, "muPaS": 4.67e-4, "prandtl": 2.99},
        "cold": {"rhoKgM3": 998.2, "cpJKgK": 4182, "muPaS": 1.002e-3, "prandtl": 7.01},
        "solid": {"rhoKgM3": 2700, "cpJKgK": 900, "kWMK": 237},
    }
    document["payload"]["numerics"] = {
        "energyCouplingIters": 25,
        "hTol": 1e-6,
        "writePrecision": 10,
        "relaxation": {"fluid": {"rho": 1.0, "pRgh": 0.7, "U": 0.3, "h": 0.5}, "solid": {"h": 0.7}},
    }
    return document


@pytest.fixture
def patch_map(repo_root):
    return json.loads((repo_root / "contract/fixtures/patch-map/accept/phase-c-cl2.json").read_text())


@pytest.fixture
def convergence(solve_document):
    return copy.deepcopy(solve_document["payload"]["convergence"])


def stable_rows(count: int = 600):
    rows = []
    for iteration in range(1, count + 1):
        rows.append({
            "iter": iteration,
            "dpHotPa": 200.0,
            "dpColdPa": 250.0,
            "qHotW": -1700.0,
            "qColdW": 1700.0,
            "tOutHotK": 315.0,
            "tOutColdK": 310.0,
            "energyBalancePct": 0.0,
            "massHotPct": 0.0,
            "massColdPct": 0.0,
            "monitor_mdotHotInSignedKgS": -0.02,
            "monitor_mdotHotOutSignedKgS": 0.02,
            "monitor_mdotColdInSignedKgS": -0.02,
            "monitor_mdotColdOutSignedKgS": 0.02,
            "residual_hot_U": 1e-3,
        })
    return rows
