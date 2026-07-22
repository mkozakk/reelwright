from pathlib import Path

import pytest

from renderer.compile import compile_plan
from renderer.edit_plan.validate import load_plan
from renderer.ffmpeg_run import run_ffmpeg
from renderer.probe import probe_file
from renderer.segments import build_concat_plan, build_segment_plan, clip_output_duration

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def test_clip_output_duration_accounts_for_speed():
    plan = load_plan(SAMPLE_DIR / "plan_full.json")
    slow_mo = plan.clips[1]
    assert slow_mo.speed == 0.75
    assert clip_output_duration(slow_mo) == pytest.approx((6.5 - 4.0) / 0.75)


def test_build_segment_plan_strips_everything_but_the_one_clip():
    plan = load_plan(SAMPLE_DIR / "plan_full.json")
    segment = build_segment_plan(plan, 1)

    assert len(segment.clips) == 1
    assert segment.clips[0].source == "src1"
    assert segment.clips[0].start == 4.0
    assert segment.clips[0].end == 6.5
    assert segment.clips[0].speed == 0.75
    assert segment.clips[0].transition_out is None

    assert segment.subtitles.enabled is False
    assert segment.color.preset == "none"
    assert segment.audio.music_track is None
    # output dims carry over so Cut normalizes to the final target
    assert segment.output.aspect == plan.output.aspect
    assert segment.output.resolution == plan.output.resolution


def test_build_concat_plan_rewrites_sources_and_keeps_creative_fields():
    plan = load_plan(SAMPLE_DIR / "plan_full.json")
    durations = [clip_output_duration(c) for c in plan.clips]
    concat = build_concat_plan(plan, durations)

    assert [c.source for c in concat.clips] == ["clip0", "clip1", "clip2"]
    assert all(c.start == 0.0 for c in concat.clips)
    assert all(c.speed == 1.0 for c in concat.clips)
    assert [c.end for c in concat.clips] == durations
    # transitions/color/subtitles/audio are the original plan's, unchanged
    assert concat.clips[1].transition_out.type == "fade_to_black"
    assert concat.color.preset == plan.color.preset
    assert concat.audio.music_track == plan.audio.music_track
    assert concat.subtitles.enabled == plan.subtitles.enabled


@pytest.mark.media
def test_cut_then_render_via_segments_matches_direct_compile():
    plan = load_plan(SAMPLE_DIR / "plan_transitions.json")
    plan.subtitles.enabled = False  # subtitle retiming across cuts isn't built yet
    sources = {"src1": SAMPLE_DIR / "clip_a.mp4", "src2": SAMPLE_DIR / "clip_b.mp4"}

    durations: list[float] = []
    cut_dir = OUT_DIR / "segments"
    cut_paths: dict[str, Path] = {}
    for i, clip in enumerate(plan.clips):
        segment_plan = build_segment_plan(plan, i)
        segment_out = cut_dir / f"{i:03d}.mp4"
        segment_out.parent.mkdir(parents=True, exist_ok=True)
        command = compile_plan(segment_plan, {clip.source: sources[clip.source]}, segment_out)
        run_ffmpeg(command.args)
        assert segment_out.exists()
        durations.append(clip_output_duration(clip))
        cut_paths[f"clip{i}"] = segment_out

    concat_plan = build_concat_plan(plan, durations)
    final_out = OUT_DIR / "segments_concat.mp4"
    command = compile_plan(concat_plan, cut_paths, final_out)
    run_ffmpeg(command.args)

    assert final_out.exists()
    assert final_out.stat().st_size > 0
    result = probe_file(final_out)
    assert result.duration == pytest.approx(sum(durations) - plan.clips[0].transition_out.duration, abs=0.3)
