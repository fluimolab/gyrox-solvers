from __future__ import annotations

import os
from pathlib import Path

import pytest


TITLES = {
    "test_bytepreserve": "[MC-01] byte preservation",
    "test_report": "[MC-02] report schema",
    "test_hashcase": "[MC-03] combined hash transcription",
    "test_meshqa": "[MC-04] quality verdict",
    "test_qoi_scan": "[MC-05] diagnostic vocabulary scan",
    "test_pins": "[MC-06] runtime pins",
    "test_port_topology": "[MC-07] counterflow-x topology",
    "test_generated_contract": "[MC-08] generated contract consumer",
}


def pytest_collection_modifyitems(items):
    for item in items:
        title = TITLES.get(item.path.stem)
        if title:
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
