from __future__ import annotations

from .edit_plan.models import Clip, EditPlan


def clip_output_duration(clip: Clip) -> float:
    return (clip.end - clip.start) / clip.speed


def build_segment_plan(plan: EditPlan, clip_index: int) -> EditPlan:
    """One-clip plan for Cut: extracts exactly that clip's source range,
    normalized to the job's target aspect/resolution. Transitions, color,
    subtitles and music are dropped here -- they apply once, at Render,
    after the segments are concatenated."""
    segment_clip = plan.clips[clip_index].model_copy(update={"transition_out": None})
    return EditPlan(
        version=plan.version,
        summary=plan.summary,
        clips=[segment_clip],
        output=plan.output.model_copy(),
    )


def build_concat_plan(plan: EditPlan, clip_durations: list[float]) -> EditPlan:
    """Rewrites clips to reference already-cut segment sources 'clip0',
    'clip1', ... at their known output duration, so Render is a plain
    compile_plan() call over pre-cut, pre-normalized inputs. Transitions,
    color, subtitles and audio are preserved unchanged from the original
    plan -- they were deliberately stripped out of each Cut invocation and
    belong here instead."""
    concat_clips = [
        clip.model_copy(
            update={
                "source": f"clip{i}",
                "start": 0.0,
                "end": clip_durations[i],
                "speed": 1.0,
            }
        )
        for i, clip in enumerate(plan.clips)
    ]
    return EditPlan(
        version=plan.version,
        summary=plan.summary,
        clips=concat_clips,
        subtitles=plan.subtitles.model_copy(),
        color=plan.color.model_copy(),
        audio=plan.audio.model_copy(),
        output=plan.output.model_copy(),
    )
