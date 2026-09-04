from __future__ import annotations

import subprocess
import sys

import pytest

TOP_LEVEL_MODULES = [
    "blade_detection.rules",
    "blade_detection.recognition",
    "blade_detection.recognition.reference_data",
    "blade_detection.api",
]


@pytest.mark.parametrize("module", TOP_LEVEL_MODULES)
def test_module_imports_in_a_fresh_interpreter(module: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
