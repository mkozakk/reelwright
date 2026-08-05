# Orchestration (`sfn.tf`, `statemachine.asl.json.tpl`)

One Standard Step Functions state machine, its ASL rendered from `statemachine.asl.json.tpl` via `templatefile()`, with Lambda ARNs, the ECS cluster/task-def ARNs, subnet ids, the jobs table name, and the DLQ URL all interpolated in. See `services/docs/pipeline.md` for the actual state-by-state walk; this doc covers the infra-level pieces around it.

## Logging and tracing

`logging_configuration` ships `ERROR`-level execution logs, with execution data included, to a dedicated CloudWatch log group. `tracing_configuration.enabled = true` makes Step Functions itself write the pipeline's root X-Ray segment, the trace every downstream `tracing.segment()` call attaches to.

## The dead-letter queue

A 14-day-retention SQS queue. Every state's `Catch` routes through `JobFailed` to `PublishJobFailedEvent` to `SendToDlq` to `Fail`, so the DLQ always ends up holding the full execution input for the failure. A `dlq_depth` CloudWatch alarm (`ApproximateNumberOfMessagesVisible > 0`) fires on anything landing there, since an unwatched DLQ is a black hole. A second alarm, `pipeline_execution_failures`, fires on any `ExecutionsFailed > 0` in the last hour, catching failures that never made it as far as the DLQ path.

## Render retry shape

`RenderSpot`'s own `Retry` is `MaxAttempts: 1`, failing fast rather than retrying Spot against Spot, with its `Catch` routing to `RenderOnDemand`. The fallback is a different capacity provider, not a retry loop. `AcquireRenderSlot`'s `Retry` on `SlotUnavailable` (10s interval, up to 90 attempts, no backoff) is the concurrency wait itself: no compute runs while a job is queued for a render slot.

## Related

- [[compute]]: the Lambdas and Fargate task this state machine invokes
- [[cost-guardrails]]: the CloudWatch dashboard visualizing this state machine's metrics
