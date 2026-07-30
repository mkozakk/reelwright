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
    sources: dict[str, dict], n: int, half_window: float
) -> list[dict]:
    # rank peaks globally across all sources, but keep the overlap check
    # scoped per source -- two windows on different sources never "overlap",
    # matching the validator's own per-source overlap rule (docs/phases/phase-8.md)
    all_points = [
        (src_id, point) for src_id, data in sources.items() for point in data["points"]
    ]
    ranked = sorted(all_points, key=lambda sp: sp[1]["level_db"], reverse=True)
    selected: list[dict] = []
    selected_by_source: dict[str, list[dict]] = {}

    for src_id, point in ranked:
        if len(selected) >= n:
            break

        source_duration = sources[src_id]["duration"]
        t = point["t"]
        start = max(0.0, t - half_window)
        end = min(source_duration, t + half_window)
        if end - start < MIN_CLIP_SECONDS:
            continue  # window collapses too small at a source boundary

        existing = selected_by_source.get(src_id, [])
        overlaps_existing = any(start < clip["end"] and clip["start"] < end for clip in existing)
        if overlaps_existing:
            continue  # skip a lower-ranked peak rather than shrinking it

        clip = {
            "source": src_id,
            "start": start,
            "end": end,
            "reason": f"loudness peak {point['level_db']:.1f} dB at {t:.1f}s",
            "speed": 1.0,
        }
        selected.append(clip)
        selected_by_source.setdefault(src_id, []).append(clip)

    return selected


def build_plan(sources: dict[str, dict], prefs: dict) -> dict:
    """sources: {src_id: {"points": list[dict], "duration": float}} for every
    video source in the session (docs/phases/phase-8.md) -- ranks loudness
    peaks globally across sources, so the fallback montage interleaves clips
    from whichever sources have the loudest moments, not just the first one."""
    prefs = prefs or {}
    budget = prefs.get("max_duration", 60.0)

    all_points = [p for data in sources.values() for p in data["points"]]
    if not all_points:
        first_src = next(iter(sources))
        clips = [_fallback_single_clip(first_src, budget)]
    else:
        n = max(1, min(MAX_CLIPS, math.floor(budget / (2 * CLIP_HALF_WINDOW_SECONDS))))
        # n floors to at least 1 even when budget is under one full +/-3s clip --
        # shrink the window to fit rather than emit a clip over max_duration.
        half_window = min(CLIP_HALF_WINDOW_SECONDS, budget / (2 * n))
        clips = _select_peak_clips(sources, n, half_window)
        if not clips:
            first_src = next(iter(sources))
            clips = [_fallback_single_clip(first_src, budget)]
        clips.sort(key=lambda clip: (clip["source"], clip["start"]))

    return {
        "version": "1",
        "summary": "Fallback plan (no-LLM): top loudness peaks",
        "clips": clips,
        "subtitles": {"enabled": False},
        "color": {"preset": "none"},
        "audio": {"music_track": None, "duck_under_speech": False},
        "output": {"max_duration": budget},
    }
