"""Strict-typing gate: mypy must pass on the integration package."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_mypy_passes_on_integration() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "custom_components/leassh"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
