from pathlib import Path

import pytest

from renderer.compile import compile_plan
from renderer.edit_plan.models import Clip, EditPlan, OutputConfig
from renderer.ffmpeg_run import run_ffmpeg
from renderer.probe import probe_file

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"

# clip_a.mp4 is 1280x720 @ 30fps; clip_c_960x540_24fps.mp4 is a deliberately
# different profile (960x540 @ 24fps) generated from it -- a stand-in for a
# real 1080p30 + 4K60 mismatch (docs/phases/phase-8.md T24), kept small to
# avoid bloating the repo with a literal 4K fixture.
_SRC_A = "clip_a.mp4"
_SRC_C = "clip_c_960x540_24fps.mp4"


@pytest.mark.media
def test_probe_confirms_the_fixtures_have_different_profiles():
    a = probe_file(SAMPLE_DIR / _SRC_A)
    c = probe_file(SAMPLE_DIR / _SRC_C)
    assert (a.width, a.height, a.fps) != (c.width, c.height, c.fps)


@pytest.mark.media
def test_a_two_source_plan_with_mismatched_profiles_concatenates_cleanly():
    plan = EditPlan(
        version="1",
        summary="mixed-profile session",
        clips=[
            Clip(source="src1", start=0.5, end=2.5, reason="src1 moment"),
            Clip(source="src2", start=0.5, end=2.5, reason="src2 moment"),
        ],
        output=OutputConfig(aspect="16:9", resolution="720p", max_duration=10.0),
    )
    sources = {"src1": SAMPLE_DIR / _SRC_A, "src2": SAMPLE_DIR / _SRC_C}
    output = OUT_DIR / "mixed_profile.mp4"

    command = compile_plan(plan, sources, output)
    run_ffmpeg(command.args)  # must not raise an xfade/concat dimension-mismatch error

    assert output.exists()
    assert output.stat().st_size > 0

    result = probe_file(output)
    assert (result.width, result.height, result.fps) == (1280, 720, 30.0)  # normalized to plan.output
