from __future__ import annotations

from . import prompt, schema_tool

DEFAULT_MODEL_ID = "amazon.nova-lite-v1:0"
DEFAULT_MAX_OUTPUT_TOKENS = 2000
DEFAULT_TEMPERATURE = 0.4


class BedrockPlanner:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        region: str | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        client=None,
    ):
        self.model_id = model_id
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.guardrail_id = guardrail_id
        self.guardrail_version = guardrail_version
        self._client = client
        self._region = region
        self._tool_schema = schema_tool.edit_plan_tool_schema()

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def generate(self, messages: list[dict]) -> tuple[dict | None, dict]:
        kwargs = {
            "modelId": self.model_id,
            "system": [{"text": prompt.SYSTEM_PROMPT}],
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": self.max_output_tokens,
                "temperature": self.temperature,
            },
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": schema_tool.TOOL_NAME,
                            "description": schema_tool.TOOL_DESCRIPTION,
                            "inputSchema": {"json": self._tool_schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": schema_tool.TOOL_NAME}},
            },
        }
        if self.guardrail_id:
            kwargs["guardrailConfig"] = {
                "guardrailIdentifier": self.guardrail_id,
                "guardrailVersion": self.guardrail_version or "DRAFT",
            }

        resp = self.client.converse(**kwargs)
        usage = resp.get("usage", {})
        return _tool_input(resp), {
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
        }


def _tool_input(resp: dict) -> dict | None:
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    return None
