# Analytics Sink (`analytics_sink/`)

Subscribed to the EventBridge bus for every `job.*` event (`created`, `planned`, `rendered`, `failed`). Writes the full matched event envelope verbatim as one JSON object per event to `s3://<analytics-bucket>/events/<event-id>.json`; Athena queries it directly via the OpenX JSON SerDe.

## Why raw JSON instead of Firehose

This intentionally replaces an originally planned Kinesis Firehose to S3 (Parquet) to Athena path. Firehose needs a per-account service subscription this account doesn't have, and at this event volume a JSON object per event is plenty, with no format-conversion step required. The rationale is recorded as a comment at the top of `handler.py`, which is the authoritative record if anything elsewhere still describes the original Firehose design.

## Related

- [[pipeline]]: the source of every `job.*` event this service ingests
- [[job-api]], [[plan]], [[render]], [[finish]]: the services that call `services/common/events.py`'s `publish()` and generate the events this service sinks
