import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

OUT_DIR = REPO_ROOT / "out"
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"


@pytest.fixture(scope="session", autouse=True)
def _ensure_out_dir():
    OUT_DIR.mkdir(exist_ok=True)
