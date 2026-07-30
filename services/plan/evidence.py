from __future__ import annotations

MAX_SCENES = 40  # absolute ceiling on scene cuts per source in the bundle
MIN_SECONDS_PER_SCENE = 2.0  # scdet runs at a low threshold and floods short
# low-motion clips with noise cuts; cap the count relative to length so the
# model gets a handful of real boundaries, not one per frame
PHRASE_GAP_SECONDS = 0.6
_SAMPLE_INTERVAL_SECONDS = 1.0

USER_PREF_KEYS = ("vibe", "max_duration", "aspect", "subtitles_enabled")


def build_evidence(per_source: dict[str, dict], music_tracks: list[dict] | None = None) -> dict:
    """per_source: {src_id: {"loudness": {...}|None, "scenes": {...}|None,
    "transcript": {...}|None}}. Every phrase/loudness point/scene cut carries
    its source id so the LLM can reason and select clips across sources
    (docs/phases/phase-8.md); MAX_SCENES/MIN_SECONDS_PER_SCENE capping is
    applied per source so one long source can't starve the others' evidence."""
    sources: list[dict] = []
    loudness_points: list[dict] = []
    scene_cuts: list[dict] = []
    phrases: list[dict] = []

    for src_id, artifacts in per_source.items():
        artifacts = artifacts or {}
        points = (artifacts.get("loudness") or {}).get("points", [])
        cuts = (artifacts.get("scenes") or {}).get("cuts", [])
        words = (artifacts.get("transcript") or {}).get("words", [])
        duration = _source_duration(points)

        sources.append({"id": src_id, "duration": duration})
        loudness_points += [
            {"source": src_id, "t": round(p["t"], 1), "level_db": round(p["level_db"], 1)}
            for p in points
        ]
        scene_cuts += [{"source": src_id, **c} for c in _top_scenes(cuts, duration)]
        phrases += [{"source": src_id, **ph} for ph in _phrases_from_words(words)]

    return {
        "sources": sources,
        "music_tracks": music_tracks or [],
        "loudness_points": loudness_points,
        "scene_cuts": scene_cuts,
        "phrases": phrases,
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
