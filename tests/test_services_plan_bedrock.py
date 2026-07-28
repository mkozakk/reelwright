from __future__ import annotations

from services.plan.bedrock_planner import BedrockPlanner

VALID_RESPONSE = {
    "output": {"message": {"content": [{"toolUse": {"input": {"version": "1", "clips": []}}}]}},
    "usage": {"inputTokens": 100, "outputTokens": 20},
}


class RecordingClient:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def converse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_generate_forces_the_edit_plan_tool_and_returns_usage():
    client = RecordingClient(VALID_RESPONSE)
    planner = BedrockPlanner(client=client)

    raw, usage = planner.generate([{"role": "user", "content": [{"text": "hi"}]}])

    assert raw == {"version": "1", "clips": []}
    assert usage == {"input_tokens": 100, "output_tokens": 20}
    tool_cfg = client.kwargs["toolConfig"]
    assert tool_cfg["toolChoice"] == {"tool": {"name": "emit_edit_plan"}}
    assert client.kwargs["inferenceConfig"]["maxTokens"] == planner.max_output_tokens


def test_guardrail_config_is_attached_only_when_configured():
    without = RecordingClient(VALID_RESPONSE)
    BedrockPlanner(client=without).generate([])
    assert "guardrailConfig" not in without.kwargs

    with_guard = RecordingClient(VALID_RESPONSE)
    BedrockPlanner(client=with_guard, guardrail_id="gr-123", guardrail_version="2").generate([])
    assert with_guard.kwargs["guardrailConfig"] == {
        "guardrailIdentifier": "gr-123",
        "guardrailVersion": "2",
    }


def test_missing_tool_use_returns_none():
    # a guardrail-blocked response carries no toolUse block -> planner yields
    # None, which the handler treats as a validation miss (retry then fallback)
    blocked = {
        "output": {"message": {"content": [{"text": "blocked by content policy"}]}},
        "usage": {"inputTokens": 50, "outputTokens": 5},
    }
    raw, usage = BedrockPlanner(client=RecordingClient(blocked)).generate([])
    assert raw is None
    assert usage == {"input_tokens": 50, "output_tokens": 5}
