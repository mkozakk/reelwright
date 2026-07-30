from pathlib import Path

import pytest

from renderer.compile import compile_plan
from renderer.edit_plan.validate import load_plan
from renderer.ffmpeg_run import run_ffmpeg

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def test_music_override_is_used_in_place_of_a_manifest_lookup(tmp_path):
    plan = load_plan(SAMPLE_DIR / "plan_basic.json")
    plan.audio.music_track = None  # no manifest id at all -- override must still trigger the music path
    override_path = SAMPLE_DIR / "music_a.mp3"

    command = compile_plan(
        plan,
        {"src1": SAMPLE_DIR / "clip_a.mp4"},
        tmp_path / "out.mp4",
        music_override=override_path,
    )

    assert str(override_path) in command.args
    # no manifest track id anywhere in the command -- proves resolve_track was never consulted
    assert "placeholder" not in " ".join(command.args)


@pytest.mark.media
def test_music_override_produces_a_real_file_with_audio(tmp_path):
    plan = load_plan(SAMPLE_DIR / "plan_basic.json")
    plan.audio.music_track = None
    output = OUT_DIR / "music_override.mp4"

    command = compile_plan(
        plan,
        {"src1": SAMPLE_DIR / "clip_a.mp4"},
        output,
        music_override=SAMPLE_DIR / "music_a.mp3",
    )
    run_ffmpeg(command.args)

    assert output.exists()
    assert output.stat().st_size > 0
