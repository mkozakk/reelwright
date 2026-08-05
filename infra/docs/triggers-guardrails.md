# Analysis Triggers & Guardrails (`eventbridge.tf`, `bedrock.tf`)

## The dev EventBridge trigger

`eventbridge.tf` holds the single-file dev convenience path: an `aws_cloudwatch_event_rule` on S3 `Object Created` under `raw/`, wired to the `trigger` Lambda. It's entirely gated behind `var.enable_dev_eventbridge_trigger` (`count = ... ? 1 : 0`), since it bypasses the HeadObject and size re-check a real `POST /jobs/{id}/start` does, and it must stay off outside dev.

## The Bedrock guardrail

`bedrock.tf` defines one Guardrail, in the `aws.bedrock` provider alias, applied to the planning call: prompt-attack and harmful-content filters (`HIGH`/`MEDIUM` strengths per category) plus PII anonymization (`NAME`, `EMAIL`, `PHONE`, `ADDRESS`, `AGE`, and card/SSN/password entities masked to placeholders rather than blocked, since a birthday-party montage legitimately contains names).

A numbered `aws_bedrock_guardrail_version` is what the `plan` Lambda actually applies (`GUARDRAIL_VERSION` env var), since `DRAFT` keeps moving as the guardrail is edited while a numbered version doesn't. This is defense-in-depth on top of, never instead of, Edit Plan schema validation.

## Related

- [[trigger]] (services): the Lambda this dev-only rule invokes
- [[plan]] (services): the Lambda that applies the Bedrock guardrail
- [[edit-plan]] (renderer): the schema validation this guardrail complements but never replaces
