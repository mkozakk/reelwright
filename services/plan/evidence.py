from __future__ import annotations

MAX_SCENES = 40  # absolute ceiling on scene cuts in the bundle
MIN_SECONDS_PER_SCENE = 2.0  # scdet runs at a low threshold and floods short
# low-motion clips with noise cuts; cap the count relative to length so the
# model gets a handful of real boundaries, not one per frame
PHRASE_GAP_SECONDS = 0.6
_SAMPLE_INTERVAL_SECONDS = 1.0

USER_PREF_KEYS = ("vibe", "max_duration", "aspect", "subtitles_enabled")


def build_evidence(
    loudness: dict | None,
    scenes: dict | None,
    transcript: dict | None,
    source_ids: list[str] | None = None,
    music_tracks: list[dict] | None = None,
) -> dict:
    loudness_points = (loudness or {}).get("points", [])
    scene_cuts = (scenes or {}).get("cuts", [])
    words = (transcript or {}).get("words", [])
    duration = _source_duration(loudness_points)
    return {
        "sources": source_ids or [],
        "music_tracks": music_tracks or [],
        "source_duration": duration,
        "loudness_points": [
            {"t": round(p["t"], 1), "level_db": round(p["level_db"], 1)} for p in loudness_points
        ],
        "scene_cuts": _top_scenes(scene_cuts, duration),
        "phrases": _phrases_from_words(words),
    }


def prefs_for_prompt(prefs: dict | None) -> dict:
    prefs = prefs or {}
    return {k: prefs[k] for k in USER_PREF_KEYS if k in prefs}


def _source_duration(points: list[dict]) -> float:
    if not points:
        return 0.0
    return round(max(p["t"] for p in points) + _SAMPLE_INTERVAL_SECONDS, 1)


def _top_scenes(cuts: list[dict], source_duration: float) -> list[dict]:
    cap = MAX_SCENES
    if source_duration > 0:
        cap = max(1, min(MAX_SCENES, int(source_duration // MIN_SECONDS_PER_SCENE)))
    ranked = sorted(cuts, key=lambda c: c.get("score", 0.0), reverse=True)[:cap]
    ranked.sort(key=lambda c: c["t"])
    return [{"t": round(c["t"], 2), "score": round(c.get("score", 0.0), 3)} for c in ranked]


def _phrases_from_words(words: list[dict], gap: float = PHRASE_GAP_SECONDS) -> list[dict]:
    phrases: list[dict] = []
    for w in words:
        text = w["text"].strip()
        if phrases and w["start"] - phrases[-1]["end"] <= gap:
            phrases[-1]["text"] += " " + text
            phrases[-1]["end"] = w["end"]
        else:
            phrases.append({"start": w["start"], "end": w["end"], "text": text})
    return phrases
