from __future__ import annotations

import json

from services.plan import schema_tool


def test_tool_schema_is_self_contained_and_nova_safe():
    schema = schema_tool.edit_plan_tool_schema()
    blob = json.dumps(schema)
    assert "$ref" not in blob
    assert "$defs" not in blob
    assert "anyOf" not in blob
    assert '"type": "null"' not in blob
    assert '"title"' not in blob
    assert '"default"' not in blob


def test_optional_fields_collapse_to_their_real_branch():
    schema = schema_tool.edit_plan_tool_schema()
    clip = schema["properties"]["clips"]["items"]
    transition = clip["properties"]["transition_out"]
    assert transition["type"] == "object"
    assert set(transition["properties"]["type"]["enum"]) == {"cut", "crossfade", "fade_to_black"}
    assert schema["properties"]["audio"]["properties"]["music_track"]["type"] == "string"


def test_required_clip_fields_survive_simplification():
    schema = schema_tool.edit_plan_tool_schema()
    required = schema["properties"]["clips"]["items"]["required"]
    assert {"source", "start", "end", "reason"} <= set(required)
