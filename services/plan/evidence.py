from __future__ import annotations

MAX_SCENES = 40  # scdet on low-motion footage emits noise; cap by score
PHRASE_GAP_SECONDS = 0.6
_SAMPLE_INTERVAL_SECONDS = 1.0

USER_PREF_KEYS = ("vibe", "max_duration", "aspect", "subtitles_enabled")


def build_evidence(
    loudness: dict | None,
    scenes: dict | None,
    transcript: dict | None,
) -> dict:
    loudness_points = (loudness or {}).get("points", [])
    scene_cuts = (scenes or {}).get("cuts", [])
    words = (transcript or {}).get("words", [])
    return {
        "source_duration": _source_duration(loudness_points),
        "loudness_points": [
            {"t": round(p["t"], 1), "level_db": round(p["level_db"], 1)} for p in loudness_points
        ],
        "scene_cuts": _top_scenes(scene_cuts),
        "phrases": _phrases_from_words(words),
    }


def prefs_for_prompt(prefs: dict | None) -> dict:
    prefs = prefs or {}
    return {k: prefs[k] for k in USER_PREF_KEYS if k in prefs}


def _source_duration(points: list[dict]) -> float:
    if not points:
        return 0.0
    return round(max(p["t"] for p in points) + _SAMPLE_INTERVAL_SECONDS, 1)


def _top_scenes(cuts: list[dict]) -> list[dict]:
    ranked = sorted(cuts, key=lambda c: c.get("score", 0.0), reverse=True)[:MAX_SCENES]
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
