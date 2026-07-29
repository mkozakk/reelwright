from __future__ import annotations

from services.plan import evidence


def test_words_group_into_phrases_on_gaps():
    transcript = {
        "words": [
            {"start": 0.5, "end": 0.9, "text": "this"},
            {"start": 0.9, "end": 1.4, "text": "is"},
            {"start": 3.2, "end": 3.6, "text": "later"},
        ]
    }
    phrases = evidence.build_evidence(None, None, transcript)["phrases"]
    assert [p["text"] for p in phrases] == ["this is", "later"]
    assert phrases[0]["start"] == 0.5 and phrases[0]["end"] == 1.4


def test_scene_cuts_capped_by_score_then_sorted_by_time():
    cuts = [{"t": float(i), "score": i / 100} for i in range(evidence.MAX_SCENES + 10)]
    scenes = evidence.build_evidence(None, {"cuts": cuts}, None)["scene_cuts"]
    assert len(scenes) == evidence.MAX_SCENES
    assert scenes == sorted(scenes, key=lambda c: c["t"])
    kept_times = {c["t"] for c in scenes}
    assert min(kept_times) > 0  # lowest-score (earliest) cuts were dropped


def test_scene_cuts_capped_proportionally_to_source_duration():
    loudness = {"points": [{"t": 0.0, "level_db": -30.0}, {"t": 7.0, "level_db": -10.0}]}  # 8s
    cuts = [{"t": i * 0.1, "score": i / 100} for i in range(60)]
    scenes = evidence.build_evidence(loudness, {"cuts": cuts}, None)["scene_cuts"]
    assert len(scenes) == 4  # 8s // MIN_SECONDS_PER_SCENE (2s)


def test_source_duration_from_loudness_tail():
    loudness = {"points": [{"t": 0.0, "level_db": -30.0}, {"t": 7.0, "level_db": -10.0}]}
    ev = evidence.build_evidence(loudness, None, None)
    assert ev["source_duration"] == 8.0


def test_missing_transcript_yields_no_phrases():
    ev = evidence.build_evidence({"points": []}, {"cuts": []}, None)
    assert ev["phrases"] == []


def test_prefs_for_prompt_filters_to_user_owned_keys():
    prefs = {"vibe": "energetic", "max_duration": 30.0, "aspect": "16:9", "internal_x": 1}
    assert evidence.prefs_for_prompt(prefs) == {
        "vibe": "energetic",
        "max_duration": 30.0,
        "aspect": "16:9",
    }
