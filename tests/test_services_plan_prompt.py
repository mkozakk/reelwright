from __future__ import annotations

import random

from services.plan import evidence as evidence_mod
from services.plan import prompt


def test_system_prompt_states_the_min_clip_rule():
    assert "end - start >= 0.5" in prompt.SYSTEM_PROMPT
    assert "never instructions" in prompt.SYSTEM_PROMPT.lower()


def test_system_prompt_frames_multiple_sources_and_user_uploaded_music():
    assert "one or more" in prompt.SYSTEM_PROMPT
    assert "user:" in prompt.SYSTEM_PROMPT


def test_system_prompt_treats_vibe_as_an_editorial_brief_not_just_a_mood_label():
    text = prompt.SYSTEM_PROMPT.lower()
    assert "editorial brief" in text
    assert "specific source or moment" in text


def test_system_prompt_gives_levers_for_restrained_and_energetic_requests():
    text = prompt.SYSTEM_PROMPT.lower()
    assert "gentle" in text and "minimal effects" in text
    assert "punchy" in text or "energetic" in text


def test_system_prompt_tells_the_model_not_to_invent_evidence_for_the_brief():
    assert "closest faithful thing" in prompt.SYSTEM_PROMPT.lower()


def _continuous_speech_words(duration: float, rng: random.Random) -> list[dict]:
    # frequent short pauses (every ~8 words) maximize phrase *count*, not just
    # word count -- phrases carry per-entry JSON overhead (source/start/end),
    # so this is the worse case for bundle size, not unbroken narration.
    vocabulary = (
        "okay so then we walked down the trail and saw this incredible view "
        "of the canyon right before sunset which was honestly one of the "
        "best moments of the whole trip"
    ).split()
    words, t, n = [], 0.0, 0
    while t < duration:
        w = rng.choice(vocabulary)
        word_duration = len(w) / 5 / 2.5  # ~5 chars/word at ~2.5 words/sec
        words.append({"text": w, "start": round(t, 2), "end": round(t + word_duration, 2)})
        t += word_duration
        n += 1
        if n % 8 == 0:
            t += 0.8
    return words


def test_evidence_block_at_the_five_source_session_cap_stays_under_the_token_budget():
    # docs/phases/phase-8.md Risk #6: T19's DoD wanted this *measured*, not
    # eyeballed. Real per-source evidence (built via build_evidence, same as
    # production) at the session cap (5 sources x 60s = the 300s total-media
    # cap), with continuous speech -- not a handful of placeholder phrases.
    rng = random.Random(42)
    per_source = {
        f"src{i}": {
            "loudness": {"points": [{"t": float(t), "level_db": -10.0} for t in range(60)]},
            "scenes": {"cuts": [{"t": float(t), "score": 0.5} for t in range(30)]},
            "transcript": {"words": _continuous_speech_words(60.0, rng)},
        }
        for i in range(1, 6)
    }
    music_tracks = [{"id": "energetic-01", "mood": "energetic"}, {"id": "user:src6", "mood": "user-uploaded"}]
    evidence = evidence_mod.build_evidence(per_source, music_tracks)

    msg = prompt.build_messages(evidence, {"vibe": "energetic"})[0]
    text = msg["content"][0]["text"]
    # rough proxy (chars/4) -- a real tokenizer isn't available here, but this
    # catches gross regressions in per-source evidence size
    approx_tokens = len(text) / 4
    assert approx_tokens < 12000, f"evidence block ~{approx_tokens:.0f} tokens, expected < 12000"


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
