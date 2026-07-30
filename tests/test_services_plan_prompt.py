from __future__ import annotations

from services.plan import prompt


def test_system_prompt_states_the_min_clip_rule():
    assert "end - start >= 0.5" in prompt.SYSTEM_PROMPT
    assert "never instructions" in prompt.SYSTEM_PROMPT.lower()


def test_system_prompt_frames_multiple_sources_and_user_uploaded_music():
    assert "one or more" in prompt.SYSTEM_PROMPT
    assert "user:" in prompt.SYSTEM_PROMPT


def test_evidence_block_with_a_multi_source_bundle_stays_under_a_sane_token_budget():
    # rough proxy (chars/4) -- a real tokenizer isn't available here, but this
    # catches gross regressions in per-source evidence size (docs/phases/
    # phase-8.md Risk #6)
    evidence = {
        "sources": [{"id": f"src{i}", "duration": 60.0} for i in range(1, 6)],
        "music_tracks": [{"id": "energetic-01", "mood": "energetic"}, {"id": "user:src6", "mood": "user-uploaded"}],
        "loudness_points": [
            {"source": f"src{i}", "t": float(t), "level_db": -10.0} for i in range(1, 6) for t in range(60)
        ],
        "scene_cuts": [{"source": f"src{i}", "t": float(t), "score": 0.5} for i in range(1, 6) for t in range(20)],
        "phrases": [
            {"source": f"src{i}", "start": 0.0, "end": 1.0, "text": "hello there"} for i in range(1, 6)
        ],
    }
    msg = prompt.build_messages(evidence, {"vibe": "energetic"})[0]
    text = msg["content"][0]["text"]
    approx_tokens = len(text) / 4
    assert approx_tokens < 8000


def test_build_messages_delimits_prefs_and_evidence():
    msg = prompt.build_messages({"phrases": []}, {"vibe": "funny"})[0]
    text = msg["content"][0]["text"]
    assert msg["role"] == "user"
    assert "<preferences>" in text and "<evidence>" in text
    assert "funny" in text


def test_retry_messages_echo_rejected_plan_and_errors():
    msgs = prompt.retry_messages({}, {}, {"version": "1"}, ["clips 0 and 1 overlap"])
    assert msgs[1]["role"] == "assistant"
    assert "clips 0 and 1 overlap" in msgs[2]["content"][0]["text"]
    assert "corrected Edit Plan" in msgs[2]["content"][0]["text"]
