from __future__ import annotations

import pytest

from renderer.edit_plan.models import Clip, EditPlan, OutputConfig
from renderer.edit_plan.validate import EditPlanValidationError, SourceBounds, validate_plan


def _plan(clips: list[Clip], music_track: str | None = None, max_duration: float = 30.0) -> EditPlan:
    plan = EditPlan(
        version="1",
        clips=clips,
        output=OutputConfig(max_duration=max_duration),
    )
    plan.audio.music_track = music_track
    return plan


def _clip(source: str, start: float = 0.0, end: float = 2.0, **kw) -> Clip:
    return Clip(source=source, start=start, end=end, reason="evidence-backed moment", **kw)


SOURCES = {
    "src1": SourceBounds(kind="video", duration=10.0),
    "src2": SourceBounds(kind="video", duration=10.0),
    "src3": SourceBounds(kind="audio", duration=180.0),
}


def test_sources_none_skips_cross_file_checks_entirely():
    # CLI/local usage with no job context -- a clip.source with no matching
    # entry anywhere must not be rejected when sources isn't provided at all
    plan = _plan([_clip("whatever-the-cli-passed")])
    assert validate_plan(plan, sources=None) is plan


def test_unknown_clip_source_is_rejected():
    plan = _plan([_clip("src9")])
    with pytest.raises(EditPlanValidationError) as excinfo:
        validate_plan(plan, sources=SOURCES)
    assert any("unknown source" in e for e in excinfo.value.errors)


def test_clip_referencing_an_audio_only_source_is_rejected():
    plan = _plan([_clip("src3")])
    with pytest.raises(EditPlanValidationError) as excinfo:
        validate_plan(plan, sources=SOURCES)
    assert any("no video stream" in e for e in excinfo.value.errors)


def test_clip_end_beyond_source_duration_is_rejected():
    plan = _plan([_clip("src1", start=8.0, end=12.0)])
    with pytest.raises(EditPlanValidationError) as excinfo:
        validate_plan(plan, sources=SOURCES)
    assert any("exceeds source" in e for e in excinfo.value.errors)


def test_clip_within_source_duration_is_accepted():
    plan = _plan([_clip("src1", start=8.0, end=9.5)])
    assert validate_plan(plan, sources=SOURCES) is plan


def test_unknown_user_music_asset_is_rejected():
    plan = _plan([_clip("src1")], music_track="user:src9")
    with pytest.raises(EditPlanValidationError) as excinfo:
        validate_plan(plan, sources=SOURCES)
    assert any("unknown uploaded asset" in e for e in excinfo.value.errors)


def test_user_music_asset_pointing_at_a_video_source_is_rejected():
    # "user:<id>" must resolve to an audio-kind source, not just any known id
    plan = _plan([_clip("src1")], music_track="user:src2")
    with pytest.raises(EditPlanValidationError) as excinfo:
        validate_plan(plan, sources=SOURCES)
    assert any("unknown uploaded asset" in e for e in excinfo.value.errors)


def test_known_user_music_asset_is_accepted():
    plan = _plan([_clip("src1")], music_track="user:src3")
    assert validate_plan(plan, sources=SOURCES) is plan


def test_bundled_music_track_is_unaffected_by_source_checks():
    # a non-"user:" track id is validated elsewhere (plan/handler.py's
    # _drop_unknown_music against the bundled manifest), not here
    plan = _plan([_clip("src1")], music_track="placeholder-energetic")
    assert validate_plan(plan, sources=SOURCES) is plan


def test_a_full_multi_source_plan_validates_cleanly():
    plan = _plan(
        [
            _clip("src1", start=0.0, end=2.0),
            _clip("src2", start=1.0, end=3.0),
            _clip("src1", start=5.0, end=6.0),
        ],
        music_track="user:src3",
        max_duration=10.0,
    )
    assert validate_plan(plan, sources=SOURCES) is plan


def test_overlap_check_still_scoped_per_source_with_cross_file_checks_active():
    # two clips on different sources at the same timestamps must not be
    # flagged as overlapping (renderer/edit_plan/validate.py's existing
    # by_source grouping, unaffected by the new checks)
    plan = _plan([_clip("src1", start=0.0, end=2.0), _clip("src2", start=0.0, end=2.0)])
    assert validate_plan(plan, sources=SOURCES) is plan
