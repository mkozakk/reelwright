from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .models import Clip, EditPlan

CONTRAST_RANGE = (0.9, 1.2)
SATURATION_RANGE = (0.8, 1.4)
BRIGHTNESS_RANGE = (-0.1, 0.1)
SPEED_RANGE = (0.5, 2.0)
GAIN_RANGE = (-20.0, -8.0)

MAX_CLIPS = 30
MIN_CLIP_SECONDS = 0.5
DURATION_EPSILON = 1e-6


class EditPlanValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class SourceBounds:
    kind: str  # "video" | "audio"
    duration: float


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return max(low, min(high, value))


def _clamp_numerics(plan: EditPlan) -> None:
    plan.color.adjust.contrast = _clamp(plan.color.adjust.contrast, CONTRAST_RANGE)
    plan.color.adjust.saturation = _clamp(plan.color.adjust.saturation, SATURATION_RANGE)
    plan.color.adjust.brightness = _clamp(plan.color.adjust.brightness, BRIGHTNESS_RANGE)
    plan.audio.music_gain_db = _clamp(plan.audio.music_gain_db, GAIN_RANGE)
    for clip in plan.clips:
        clip.speed = _clamp(clip.speed, SPEED_RANGE)


def _clip_output_duration(clip: Clip) -> float:
    return (clip.end - clip.start) / clip.speed


def _check_structure(plan: EditPlan) -> list[str]:
    errors: list[str] = []
    clips = plan.clips

    if not clips:
        errors.append("plan must contain at least one clip")
        return errors

    if len(clips) > MAX_CLIPS:
        errors.append(f"too many clips: {len(clips)} > {MAX_CLIPS}")

    for i, clip in enumerate(clips):
        duration = clip.end - clip.start
        if duration < MIN_CLIP_SECONDS - DURATION_EPSILON:
            errors.append(f"clip {i} is shorter than {MIN_CLIP_SECONDS}s")

        transition = clip.transition_out
        has_next = i + 1 < len(clips)
        if transition and transition.type != "cut" and transition.duration > 0 and has_next:
            next_clip = clips[i + 1]
            shorter = min(duration, next_clip.end - next_clip.start)
            if transition.duration > shorter / 2 + DURATION_EPSILON:
                errors.append(
                    f"clip {i} transition duration {transition.duration}s exceeds half of "
                    f"the shorter adjacent clip ({shorter}s)"
                )

    by_source: dict[str, list[tuple[int, Clip]]] = {}
    for i, clip in enumerate(clips):
        by_source.setdefault(clip.source, []).append((i, clip))
    for entries in by_source.values():
        for a_pos in range(len(entries)):
            i, a = entries[a_pos]
            for b_pos in range(a_pos + 1, len(entries)):
                j, b = entries[b_pos]
                overlap = a.start < b.end and b.start < a.end
                if overlap and a.speed == b.speed:
                    errors.append(f"clips {i} and {j} overlap on source '{a.source}'")

    total_output = 0.0
    for clip in clips:
        total_output += _clip_output_duration(clip)
        transition = clip.transition_out
        if transition and transition.type == "crossfade":
            total_output -= transition.duration
    if total_output > plan.output.max_duration + DURATION_EPSILON:
        errors.append(
            f"output duration {total_output:.2f}s exceeds max_duration "
            f"{plan.output.max_duration}s"
        )

    return errors


def _check_sources(plan: EditPlan, sources: dict[str, SourceBounds]) -> list[str]:
    # Invisible with one source (nothing to get wrong); with N sources an
    # unknown/audio-only/out-of-bounds clip.source would otherwise reach
    # Cut's job.sources[clip.source] as an unhandled KeyError instead of
    # being rejected here, at the plan boundary.
    errors: list[str] = []
    for i, clip in enumerate(plan.clips):
        bounds = sources.get(clip.source)
        if bounds is None:
            errors.append(f"clip {i} references unknown source '{clip.source}'")
            continue
        if bounds.kind != "video":
            errors.append(
                f"clip {i} references source '{clip.source}', which has no video stream"
            )
            continue
        if clip.end > bounds.duration + DURATION_EPSILON:
            errors.append(
                f"clip {i} end ({clip.end}s) exceeds source '{clip.source}' "
                f"duration ({bounds.duration}s)"
            )

    track = plan.audio.music_track
    if track and track.startswith("user:"):
        asset_id = track.removeprefix("user:")
        bounds = sources.get(asset_id)
        if bounds is None or bounds.kind != "audio":
            errors.append(f"audio.music_track references unknown uploaded asset '{track}'")

    return errors


def validate_plan(
    plan: EditPlan,
    prefs: dict | None = None,
    sources: dict[str, SourceBounds] | None = None,
) -> EditPlan:
    prefs = prefs or {}
    if "aspect" in prefs:
        plan.output.aspect = prefs["aspect"]
    if "max_duration" in prefs:
        plan.output.max_duration = prefs["max_duration"]

    _clamp_numerics(plan)

    errors = _check_structure(plan)
    if sources is not None:
        errors += _check_sources(plan, sources)
    if errors:
        raise EditPlanValidationError(errors)

    return plan


def load_plan(path: str | Path) -> EditPlan:
    data = json.loads(Path(path).read_text())
    try:
        return EditPlan.model_validate(data)
    except ValidationError as exc:
        raise EditPlanValidationError([str(error) for error in exc.errors()]) from exc
