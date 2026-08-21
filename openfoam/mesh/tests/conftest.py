from __future__ import annotations

import os
from pathlib import Path

import pytest


TITLES = {
    "test_bytepreserve": "[MC-01] patch-map byte-diff 0",
    "test_report": "[MC-02] convert-report 스키마 자기 검증",
    "test_hashcase": "[MC-03] meshCombinedSha _hash_case 전사",
    "test_meshqa": "[MC-04] checkMesh QA 판정기",
    "test_qoi_scan": "[MC-05] diagnostics QoI 미탑재",
    "test_pins": "[MC-06] PINNED.lock 실재·일치",
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
def gyrox_root() -> Path:
    configured = os.environ.get("GYROX_SOURCE_ROOT")
    root = Path(configured) if configured else Path(__file__).resolve().parents[4] / "gyrox"
    if not root.is_dir():
        pytest.fail(f"required Gyrox M0 source tree is absent: {root}", pytrace=False)
    return root
