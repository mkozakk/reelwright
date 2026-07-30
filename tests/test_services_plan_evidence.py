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
    phrases = evidence.build_evidence({"src1": {"transcript": transcript}})["phrases"]
    assert [p["text"] for p in phrases] == ["this is", "later"]
    assert phrases[0]["start"] == 0.5 and phrases[0]["end"] == 1.4
    assert all(p["source"] == "src1" for p in phrases)


def test_scene_cuts_capped_by_score_then_sorted_by_time():
    cuts = [{"t": float(i), "score": i / 100} for i in range(evidence.MAX_SCENES + 10)]
    scenes = evidence.build_evidence({"src1": {"scenes": {"cuts": cuts}}})["scene_cuts"]
    assert len(scenes) == evidence.MAX_SCENES
    assert scenes == sorted(scenes, key=lambda c: c["t"])
    kept_times = {c["t"] for c in scenes}
    assert min(kept_times) > 0  # lowest-score (earliest) cuts were dropped


def test_scene_cuts_capped_proportionally_to_source_duration():
    loudness = {"points": [{"t": 0.0, "level_db": -30.0}, {"t": 7.0, "level_db": -10.0}]}  # 8s
    cuts = [{"t": i * 0.1, "score": i / 100} for i in range(60)]
    scenes = evidence.build_evidence({"src1": {"loudness": loudness, "scenes": {"cuts": cuts}}})["scene_cuts"]
    assert len(scenes) == 4  # 8s // MIN_SECONDS_PER_SCENE (2s)


def test_sources_report_each_source_s_duration():
    per_source = {
        "src1": {"loudness": {"points": [{"t": 0.0, "level_db": -30.0}, {"t": 7.0, "level_db": -10.0}]}},
        "src2": {"loudness": {"points": [{"t": 0.0, "level_db": -20.0}, {"t": 2.0, "level_db": -15.0}]}},
    }
    ev = evidence.build_evidence(per_source)
    assert ev["sources"] == [{"id": "src1", "duration": 8.0}, {"id": "src2", "duration": 3.0}]


def test_missing_transcript_yields_no_phrases():
    ev = evidence.build_evidence({"src1": {"loudness": {"points": []}, "scenes": {"cuts": []}}})
    assert ev["phrases"] == []


def test_evidence_tags_every_item_with_its_source_and_merges_across_sources():
    per_source = {
        "src1": {
            "loudness": {"points": [{"t": 0.0, "level_db": -10.0}]},
            "scenes": {"cuts": [{"t": 0.0, "score": 0.9}]},
            "transcript": {"words": [{"start": 0.0, "end": 0.5, "text": "hi"}]},
        },
        "src2": {
            "loudness": {"points": [{"t": 1.0, "level_db": -5.0}]},
            "scenes": {"cuts": [{"t": 1.0, "score": 0.8}]},
            "transcript": {"words": [{"start": 1.0, "end": 1.5, "text": "yo"}]},
        },
    }
    ev = evidence.build_evidence(per_source, music_tracks=[{"id": "user:src3", "mood": "user-uploaded"}])

    assert {p["source"] for p in ev["loudness_points"]} == {"src1", "src2"}
    assert {c["source"] for c in ev["scene_cuts"]} == {"src1", "src2"}
    assert {p["source"] for p in ev["phrases"]} == {"src1", "src2"}
    assert ev["music_tracks"] == [{"id": "user:src3", "mood": "user-uploaded"}]


def test_prefs_for_prompt_filters_to_user_owned_keys():
    prefs = {"vibe": "energetic", "max_duration": 30.0, "aspect": "16:9", "internal_x": 1}
    assert evidence.prefs_for_prompt(prefs) == {
        "vibe": "energetic",
        "max_duration": 30.0,
        "aspect": "16:9",
    }
