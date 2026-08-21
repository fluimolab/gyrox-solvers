from __future__ import annotations

import importlib.metadata
import platform
from pathlib import Path

import pytest


@pytest.mark.parametrize("_case", [None], ids=["[MC-06]"])
def test_runtime_pins(_case):
    pin_path = Path(__file__).resolve().parents[1] / "PINNED.lock"
    assert pin_path.is_file()
    pins = dict(line.split("==", 1) for line in pin_path.read_text().splitlines() if "==" in line)
    assert platform.python_version() == pins["python"]
    for package in ("numpy", "jsonschema", "pytest"):
        assert importlib.metadata.version(package) == pins[package]
