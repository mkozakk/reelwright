from __future__ import annotations

import math

from renderer.edit_plan.validate import MAX_CLIPS, MIN_CLIP_SECONDS

CLIP_HALF_WINDOW_SECONDS = 3.0  # -> 6s clips, "top-N loudness peaks +/- 3s"

# renderer.loudness.downsample_to_1hz always buckets to ~1 Hz, but
# loudness_points doesn't carry that interval itself, so it's approximated
# here rather than threaded through as a 4th parameter.
_SAMPLE_INTERVAL_SECONDS = 1.0


def _fallback_single_clip(src_id: str, budget: float) -> dict:
    # No peaks to rank -- emit a single defensive opening clip. budget below
    # MIN_CLIP_SECONDS is schema-legal but unsatisfiable by any plan (the
    # validator rejects any clip shorter than that, fallback or not).
    end = max(MIN_CLIP_SECONDS, min(budget, 2 * CLIP_HALF_WINDOW_SECONDS))
    return {
        "source": src_id,
        "start": 0.0,
        "end": end,
        "reason": "no loudness signal available; using a default opening clip",
        "speed": 1.0,
    }


def _select_peak_clips(
    src_id: str, points: list[dict], source_duration: float, n: int, half_window: float
) -> list[dict]:
    ranked = sorted(points, key=lambda point: point["level_db"], reverse=True)
    selected: list[dict] = []

    for point in ranked:
        if len(selected) >= n:
            break

        t = point["t"]
        start = max(0.0, t - half_window)
        end = min(source_duration, t + half_window)
        if end - start < MIN_CLIP_SECONDS:
            continue  # window collapses too small at a source boundary

        overlaps_existing = any(start < clip["end"] and clip["start"] < end for clip in selected)
        if overlaps_existing:
            continue  # skip a lower-ranked peak rather than shrinking it

        selected.append(
            {
                "source": src_id,
                "start": start,
                "end": end,
                "reason": f"loudness peak {point['level_db']:.1f} dB at {t:.1f}s",
                "speed": 1.0,
            }
        )

    return selected


def build_plan(src_id: str, loudness_points: list[dict], prefs: dict) -> dict:
    prefs = prefs or {}
    budget = prefs.get("max_duration", 60.0)

    if not loudness_points:
        clips = [_fallback_single_clip(src_id, budget)]
    else:
        source_duration = max(point["t"] for point in loudness_points) + _SAMPLE_INTERVAL_SECONDS
        n = max(1, min(MAX_CLIPS, math.floor(budget / (2 * CLIP_HALF_WINDOW_SECONDS))))
        # n floors to at least 1 even when budget is under one full +/-3s clip --
        # shrink the window to fit rather than emit a clip over max_duration.
        half_window = min(CLIP_HALF_WINDOW_SECONDS, budget / (2 * n))
        clips = _select_peak_clips(src_id, loudness_points, source_duration, n, half_window)
        if not clips:
            clips = [_fallback_single_clip(src_id, budget)]
        clips.sort(key=lambda clip: clip["start"])

    return {
        "version": "1",
        "summary": "Fallback plan (no-LLM): top loudness peaks",
        "clips": clips,
        "subtitles": {"enabled": False},
        "color": {"preset": "none"},
        "audio": {"music_track": None, "duck_under_speech": False},
        "output": {"max_duration": budget},
    }
