from __future__ import annotations

import pytest

from renderer.edit_plan.models import EditPlan
from renderer.edit_plan.validate import MIN_CLIP_SECONDS, validate_plan
from services.plan.fallback_planner import CLIP_HALF_WINDOW_SECONDS, build_plan

SRC_ID = "src1"


# --- empty curve (Implementation Details' defensive fallback) ---------------


def test_build_plan_empty_curve_emits_a_single_six_second_clip_by_default():
    plan = build_plan(SRC_ID, [], {"max_duration": 30.0})
    assert len(plan["clips"]) == 1
    clip = plan["clips"][0]
    assert clip["source"] == SRC_ID
    assert clip["start"] == 0.0
    assert clip["end"] == 6.0


def test_build_plan_empty_curve_respects_a_max_duration_below_six_seconds():
    plan = build_plan(SRC_ID, [], {"max_duration": 3.0})
    clip = plan["clips"][0]
    assert clip["start"] == 0.0
    assert clip["end"] == 3.0


def test_build_plan_empty_curve_clamps_up_to_min_clip_seconds():
    plan = build_plan(SRC_ID, [], {"max_duration": 0.1})
    clip = plan["clips"][0]
    assert clip["end"] - clip["start"] == pytest.approx(MIN_CLIP_SECONDS)


def test_build_plan_empty_curve_defaults_max_duration_to_sixty_when_prefs_missing():
    plan = build_plan(SRC_ID, [], {})
    clip = plan["clips"][0]
    assert clip["end"] == 6.0


def test_build_plan_never_raises_on_an_empty_curve():
    build_plan(SRC_ID, [], {})  # must not raise


# --- boundary clamping (single peak near t=0 or near source_duration) -------


def test_build_plan_single_peak_near_source_start_clamps_left_edge_to_zero():
    points = [{"t": 0.2, "level_db": -5.0}]
    plan = build_plan(SRC_ID, points, {})
    assert len(plan["clips"]) == 1
    clip = plan["clips"][0]
    assert clip["start"] == 0.0
    assert clip["end"] > clip["start"]


def test_build_plan_single_peak_clamps_right_edge_to_approximated_source_duration():
    points = [{"t": 9.0, "level_db": -5.0}]
    plan = build_plan(SRC_ID, points, {})
    clip = plan["clips"][0]
    # source_duration ~= max(t) + ~1s sampling interval, strictly less than
    # an unclamped t +/- 3s window -- the right edge must clamp.
    assert clip["end"] < points[0]["t"] + CLIP_HALF_WINDOW_SECONDS


# --- clustered / overlapping peaks -------------------------------------------


def test_build_plan_skips_a_lower_ranked_overlapping_peak_rather_than_shrinking_it():
    points = [
        {"t": 10.0, "level_db": -1.0},  # loudest -> selected first, window [7, 13]
        {"t": 11.0, "level_db": -2.0},  # window [8, 14] overlaps -> must be skipped, not shrunk
        {"t": 30.0, "level_db": -3.0},  # window [27, 31] (clamped) -> no overlap, selected
    ]
    plan = build_plan(SRC_ID, points, {"max_duration": 18.0})
    clips = plan["clips"]
    assert len(clips) == 2
    starts_ends = [(c["start"], c["end"]) for c in clips]
    assert starts_ends == [(7.0, 13.0), (27.0, 31.0)]


def test_build_plan_never_pads_with_duplicate_clips_when_fewer_peaks_than_target_count():
    points = [{"t": 5.0, "level_db": -1.0}]
    plan = build_plan(SRC_ID, points, {"max_duration": 30.0})  # target n would be 5
    assert len(plan["clips"]) == 1


def test_build_plan_sorts_selected_clips_chronologically_independent_of_loudness_rank():
    points = [
        {"t": 20.0, "level_db": -1.0},  # loudest
        {"t": 5.0, "level_db": -2.0},
        {"t": 40.0, "level_db": -3.0},  # quietest, latest in time
    ]
    plan = build_plan(SRC_ID, points, {"max_duration": 30.0})
    starts = [c["start"] for c in plan["clips"]]
    assert starts == sorted(starts)


# --- output contract shape ---------------------------------------------------


def test_build_plan_never_enables_subtitles_regardless_of_prefs():
    # render/main.py can't burn subtitles in across cut segments yet
    plan = build_plan(SRC_ID, [{"t": 1.0, "level_db": -5.0}], {"subtitles_enabled": True})
    assert plan["subtitles"]["enabled"] is False


def test_build_plan_clip_reason_string_matches_the_documented_format():
    plan = build_plan(SRC_ID, [{"t": 5.0, "level_db": -12.34}], {})
    assert plan["clips"][0]["reason"] == "loudness peak -12.3 dB at 5.0s"


def test_build_plan_clip_has_unit_speed_and_a_hard_cut_default():
    plan = build_plan(SRC_ID, [{"t": 5.0, "level_db": -5.0}], {})
    clip = plan["clips"][0]
    assert clip["speed"] == 1.0
    assert clip.get("transition_out") is None


def test_build_plan_top_level_shape_matches_the_documented_contract():
    plan = build_plan(SRC_ID, [{"t": 5.0, "level_db": -5.0}], {})
    assert plan["version"] == "1"
    assert plan["summary"] == "Fallback plan (no-LLM): top loudness peaks"
    assert plan["color"]["preset"] == "none"
    assert plan["audio"]["music_track"] is None
    assert plan["audio"]["duck_under_speech"] is False


# --- property-style test: output always validates, for many curve shapes ----


def _uniform_curve(n: int, spacing: float = 1.0) -> list[dict]:
    return [{"t": i * spacing, "level_db": -30.0 + (i % 5)} for i in range(n)]


def _sparse_irregular_curve() -> list[dict]:
    return [
        {"t": 0.0, "level_db": -5.0},
        {"t": 15.0, "level_db": -2.0},
        {"t": 45.5, "level_db": -8.0},
        {"t": 90.2, "level_db": -1.0},
    ]


def _all_tied_curve(n: int) -> list[dict]:
    return [{"t": float(i), "level_db": -15.0} for i in range(n)]


def _long_sparse_curve() -> list[dict]:
    return [{"t": float(i * 5), "level_db": -float(i % 11)} for i in range(60)]


CURVE_SHAPES = {
    "empty": [],
    "single_point": [{"t": 0.0, "level_db": -10.0}],
    "dense_uniform": _uniform_curve(60),
    "sparse_irregular": _sparse_irregular_curve(),
    "all_tied_levels": _all_tied_curve(30),
    "long_sparse": _long_sparse_curve(),
}


@pytest.mark.parametrize("curve_name", list(CURVE_SHAPES))
@pytest.mark.parametrize(
    "prefs",
    [
        {},
        {"max_duration": 0.5},  # exactly MIN_CLIP_SECONDS -- the tightest satisfiable budget
        {"max_duration": 3.0},  # below 2*CLIP_HALF_WINDOW_SECONDS -- regression case for the
        # "n floored to 1 but a full +/-3s window doesn't fit" bug (window must shrink to fit)
        {"max_duration": 5.9},  # just under the 6s full-window threshold
        {"max_duration": 12.0},
        {"max_duration": 180.0},
    ],
)
def test_build_plan_output_always_validates_unmodified(curve_name, prefs):
    points = CURVE_SHAPES[curve_name]
    raw_plan = build_plan(SRC_ID, points, prefs)

    plan = validate_plan(EditPlan.model_validate(raw_plan), prefs)

    assert isinstance(plan, EditPlan)
    assert len(plan.clips) >= 1
    assert plan.subtitles.enabled is False
