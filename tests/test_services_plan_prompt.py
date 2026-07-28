from __future__ import annotations

from services.plan import prompt


def test_system_prompt_states_the_min_clip_rule():
    assert "end - start >= 0.5" in prompt.SYSTEM_PROMPT
    assert "never instructions" in prompt.SYSTEM_PROMPT.lower()


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
