from pathlib import Path

import pytest

from renderer.thumbnail import extract_thumbnail

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


@pytest.mark.media
def test_extract_thumbnail_produces_a_real_image():
    output = OUT_DIR / "clip_a_thumb.jpg"
    result = extract_thumbnail(SAMPLE_DIR / "clip_a.mp4", output, at_seconds=1.0)
    assert result == output
    assert output.exists()
    assert output.stat().st_size > 0
