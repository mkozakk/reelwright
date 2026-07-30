# job.created / job.planned / job.rendered / job.failed lifecycle events
# (services/common/events.py, JobFailed's PublishJobFailedEvent state) land
# here via EventBridge -> a small Lambda -> S3 as raw JSON, queryable from
# Athena. Originally Firehose converted these to Parquet, but Firehose needs
# a per-account service subscription this account doesn't have
# (SubscriptionRequiredException) -- at portfolio event volume, one JSON
# object per event is plenty, and Athena's OpenX JSON SerDe queries it
# directly with no format-conversion step. Separate bucket/lifecycle from
# raw/work/output (s3.tf) -- this is an audit/cost trail, not a working
# scratch area.

resource "aws_s3_bucket" "analytics" {
  bucket        = "${local.name_prefix}-analytics-${data.aws_caller_identity.current.account_id}"
  tags          = { Name = "${local.name_prefix}-analytics" }
  force_destroy = true # disposable dev infra, matches s3.tf
}

resource "aws_s3_bucket_public_access_block" "analytics" {
  bucket                  = aws_s3_bucket.analytics.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "analytics" {
  bucket = aws_s3_bucket.analytics.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# a longer window than raw/work/output (this is the audit trail, not scratch
# space) but still bounded, per docs.DESIGN.md's cost model
resource "aws_s3_bucket_lifecycle_configuration" "analytics" {
  bucket = aws_s3_bucket.analytics.id
  rule {
    id     = "expire-events"
    status = "Enabled"
    filter {}
    expiration {
      days = 180
    }
  }
}

resource "aws_glue_catalog_database" "analytics" {
  name = replace("${local.name_prefix}_analytics", "-", "_")
}

# superset schema across all four detail-types -- struct columns are
# nullable, so job.created's row just has nulls in job.planned's cost/token
# columns, etc.
resource "aws_glue_catalog_table" "events" {
  name          = "pipeline_events"
  database_name = aws_glue_catalog_database.analytics.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification = "json"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.analytics.bucket}/events/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      # EventBridge's own envelope field is "detail-type" (hyphenated); the
      # mapping.* SerDe property remaps it to a valid Hive/Athena column name.
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "mapping.detail_type" = "detail-type"
      }
    }

    columns {
      name = "version"
      type = "string"
    }
    columns {
      name = "id"
      type = "string"
    }
    columns {
      name = "detail_type"
      type = "string"
    }
    columns {
      name = "source"
      type = "string"
    }
    columns {
      name = "account"
      type = "string"
    }
    columns {
      name = "time"
      type = "string"
    }
    columns {
      name = "region"
      type = "string"
    }
    columns {
      name = "detail"
      type = "struct<job_id:string,user_id:string,status:string,timestamp:string,source:string,model:string,input_tokens:int,output_tokens:int,cost_usd:double,error:string>"
    }
  }
}

resource "aws_iam_role" "analytics_sink" {
  name               = "${local.name_prefix}-analytics-sink-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "analytics_sink_logs" {
  role       = aws_iam_role.analytics_sink.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "analytics_sink_xray" {
  role       = aws_iam_role.analytics_sink.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy" "analytics_sink" {
  name = "analytics-sink"
  role = aws_iam_role.analytics_sink.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.analytics.arn}/*"
      }
    ]
  })
}

# same shared container image as probe/plan/finish/job_api -- already has
# aws-xray-sdk and the rest of [services] installed, no separate zip to build
resource "aws_lambda_function" "analytics_sink" {
  function_name = "${local.name_prefix}-analytics-sink"
  role          = aws_iam_role.analytics_sink.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 15
  memory_size   = 128

  image_config {
    command = ["services.analytics_sink.handler.handler"]
  }

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      ANALYTICS_BUCKET = aws_s3_bucket.analytics.bucket
    }
  }
}

resource "aws_cloudwatch_event_rule" "pipeline_events" {
  name        = "${local.name_prefix}-pipeline-events"
  description = "montage-pipeline job.* lifecycle events -> analytics sink Lambda"

  # must match services/common/events.py's SOURCE constant
  event_pattern = jsonencode({
    source = ["montage.pipeline"]
  })
}

resource "aws_cloudwatch_event_target" "pipeline_events_sink" {
  rule      = aws_cloudwatch_event_rule.pipeline_events.name
  target_id = "analytics-sink"
  arn       = aws_lambda_function.analytics_sink.arn
}

# same pattern as eventbridge.tf's trigger permission -- EventBridge invokes
# via this resource-based policy, no IAM role on the target needed
resource "aws_lambda_permission" "allow_eventbridge_analytics_sink" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.analytics_sink.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pipeline_events.arn
}

resource "aws_athena_workgroup" "analytics" {
  name = "${local.name_prefix}-analytics"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false

    result_configuration {
      output_location = "s3://${aws_s3_bucket.analytics.bucket}/athena-results/"
    }
  }
}

resource "aws_athena_named_query" "jobs_per_day" {
  name      = "jobs-per-day"
  workgroup = aws_athena_workgroup.analytics.name
  database  = aws_glue_catalog_database.analytics.name
  query     = <<-SQL
    SELECT date_trunc('day', from_iso8601_timestamp(detail.timestamp)) AS day,
           detail_type,
           count(*) AS event_count
    FROM ${aws_glue_catalog_table.events.name}
    GROUP BY 1, 2
    ORDER BY 1 DESC, 2
  SQL
}

# only the 3 events on the create -> plan -> render path (job.failed isn't
# part of this happy-path timing, same scope as the docs.phases.phase-7.md
# checklist's "avg step durations" bullet)
resource "aws_athena_named_query" "avg_step_durations" {
  name      = "avg-step-durations"
  workgroup = aws_athena_workgroup.analytics.name
  database  = aws_glue_catalog_database.analytics.name
  query     = <<-SQL
    WITH lifecycle AS (
      SELECT detail.job_id AS job_id,
             detail_type,
             from_iso8601_timestamp(detail.timestamp) AS ts
      FROM ${aws_glue_catalog_table.events.name}
      WHERE detail_type IN ('job.created', 'job.planned', 'job.rendered')
    ),
    pivoted AS (
      SELECT job_id,
             min(ts) FILTER (WHERE detail_type = 'job.created')  AS created_at,
             min(ts) FILTER (WHERE detail_type = 'job.planned')  AS planned_at,
             min(ts) FILTER (WHERE detail_type = 'job.rendered') AS rendered_at
      FROM lifecycle
      GROUP BY job_id
    )
    SELECT avg(date_diff('second', created_at, planned_at))  AS avg_created_to_planned_s,
           avg(date_diff('second', planned_at, rendered_at)) AS avg_planned_to_rendered_s,
           avg(date_diff('second', created_at, rendered_at)) AS avg_created_to_rendered_s
    FROM pivoted
  SQL
}

# surfaces the Bedrock planning cost already computed by services/plan -- a
# full per-job AWS-bill breakdown (Cost Explorer resource tagging) is out of
# scope, per docs/phases/phase-7.md
resource "aws_athena_named_query" "cost_per_job" {
  name      = "cost-per-job"
  workgroup = aws_athena_workgroup.analytics.name
  database  = aws_glue_catalog_database.analytics.name
  query     = <<-SQL
    SELECT detail.job_id        AS job_id,
           detail.model         AS model,
           detail.input_tokens  AS input_tokens,
           detail.output_tokens AS output_tokens,
           detail.cost_usd      AS cost_usd
    FROM ${aws_glue_catalog_table.events.name}
    WHERE detail_type = 'job.planned'
    ORDER BY detail.cost_usd DESC
  SQL
}
