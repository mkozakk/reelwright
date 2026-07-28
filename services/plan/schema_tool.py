from __future__ import annotations

from renderer.edit_plan.models import EditPlan

TOOL_NAME = "emit_edit_plan"
TOOL_DESCRIPTION = (
    "Return the finished Edit Plan for this montage. Every clip MUST be at "
    "least 0.5s long (end - start >= 0.5); prefer 1.5-4s clips. Never exceed "
    "output.max_duration."
)


def edit_plan_tool_schema() -> dict:
    return _simplify_for_nova(_deref(EditPlan.model_json_schema()))


def _deref(schema: dict) -> dict:
    # Nova tool-use rejects $ref/$defs; inline every ref into a self-contained
    # schema (verified in tools/spikes/phase4_planning).
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = resolve(dict(defs[node["$ref"].split("/")[-1]]))
                target.update({k: resolve(v) for k, v in node.items() if k != "$ref"})
                return target
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve({k: v for k, v in schema.items() if k != "$defs"})


def _simplify_for_nova(node):
    # Nova Pro's tool call fails outright on `anyOf:[X,null]` (Optional fields)
    # and is noised by `title`/`default`; collapse nullable unions to their real
    # branch (optionality is carried by `required`) and drop the noise.
    if isinstance(node, dict):
        if "anyOf" in node:
            branches = [b for b in node["anyOf"] if b.get("type") != "null"]
            if len(branches) == 1:
                merged = {**branches[0], **{k: v for k, v in node.items() if k != "anyOf"}}
                return _simplify_for_nova(merged)
        return {k: _simplify_for_nova(v) for k, v in node.items() if k not in ("title", "default")}
    if isinstance(node, list):
        return [_simplify_for_nova(v) for v in node]
    return node
