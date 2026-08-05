# Analytics (`analytics.tf`)

Captures every `job.*` lifecycle event into a queryable form.

## The bucket and catalog

A dedicated bucket, with a 180-day expiration since it's an audit trail rather than scratch space, and a Glue catalog database/table (`pipeline_events`) with one superset schema across all four `job.*` detail-types. Struct columns are nullable, so a `job.created` row just has nulls in `job.planned`'s cost and token columns.

## Why raw JSON instead of Firehose

`analytics_sink`, a Lambda subscribed to the whole `montage.pipeline` EventBridge source, writes each matched event as one raw JSON object to `s3://.../events/<event-id>.json`. Athena queries it directly via the OpenX JSON SerDe (`mapping.detail_type` remaps EventBridge's hyphenated `detail-type` field to a valid column name).

This deliberately replaces an original Kinesis Firehose to S3 (Parquet) to Athena design. Firehose needs a per-account service subscription this account doesn't have, and at this event volume raw JSON is plenty and needs no format-conversion step.

## Pre-written queries

Three named Athena queries ship in this file:

- `jobs-per-day`: event counts grouped by day and type.
- `avg-step-durations`: average `job.created` to `job.planned` to `job.rendered` timing.
- `cost-per-job`: surfaces the Bedrock cost `plan/handler.py` already computes per job. A full AWS-bill-level cost breakdown via Cost Explorer tagging is out of scope.

## Related

- [[analytics-sink]] (services): the Lambda this file provisions
- [[cost-guardrails]]: the budget and dashboard covering infrastructure cost, distinct from this per-job cost tracking
