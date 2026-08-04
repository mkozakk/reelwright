import re
import subprocess
from pathlib import Path

import pytest

from renderer.compile import compile_plan
from renderer.edit_plan.models import Clip, EditPlan, OutputConfig
from renderer.ffmpeg_run import run_ffmpeg, ffmpeg_binary

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def _render_single_clip(source: Path, output: Path) -> Path:
    plan = EditPlan(
        version="1",
        summary="rotation regression",
        clips=[Clip(source="src", start=0.5, end=2.5, reason="rotation check")],
        output=OutputConfig(aspect="16:9", resolution="720p", max_duration=5.0),
    )
    command = compile_plan(plan, {"src": source}, output)
    run_ffmpeg(command.args)
    return output


def _average_ssim(a: Path, b: Path) -> float:
    result = subprocess.run(
        [ffmpeg_binary(), "-i", str(a), "-i", str(b), "-lavfi", "ssim", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    match = re.search(r"All:([\d.]+)", result.stderr)
    assert match, f"no SSIM output found:\n{result.stderr[-2000:]}"
    return float(match.group(1))


@pytest.mark.media
def test_a_source_with_only_rotation_metadata_renders_like_a_physically_rotated_one():
    flagged = _render_single_clip(SAMPLE_DIR / "clip_b_portrait_flagged.mp4", OUT_DIR / "rotation_flagged.mp4")
    baked = _render_single_clip(SAMPLE_DIR / "clip_b_portrait_baked.mp4", OUT_DIR / "rotation_baked.mp4")
    unrotated = _render_single_clip(SAMPLE_DIR / "clip_b.mp4", OUT_DIR / "rotation_unrotated.mp4")

    assert _average_ssim(flagged, baked) > 0.95
    assert _average_ssim(flagged, unrotated) < 0.95
