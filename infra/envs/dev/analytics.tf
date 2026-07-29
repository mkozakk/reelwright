# job.created / job.planned / job.rendered / job.failed lifecycle events
# (services/common/events.py, JobFailed's PublishJobFailedEvent state) land
# here via EventBridge -> Firehose -> Parquet, queryable from Athena. Separate
# bucket/lifecycle from raw/work/output (s3.tf) -- this is an audit/cost
# trail, not a working scratch area.

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

# superset schema across all four detail-types -- Parquet/Hive columns are
# nullable, so job.created's row just has nulls in job.planned's cost/token
# columns, etc.
resource "aws_glue_catalog_table" "events" {
  name          = "pipeline_events"
  database_name = aws_glue_catalog_database.analytics.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification = "parquet"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.analytics.bucket}/events/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    columns {
      name = "version"
      type = "string"
    }
    columns {
      name = "id"
      type = "string"
    }
    # EventBridge's own envelope field is "detail-type" (hyphenated); the
    # Firehose deserializer's column_to_json_key_mappings below remaps it to
    # this valid Hive/Parquet column name.
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

data "aws_iam_policy_document" "firehose_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "firehose_analytics" {
  name               = "${local.name_prefix}-firehose-analytics"
  assume_role_policy = data.aws_iam_policy_document.firehose_assume.json
}

resource "aws_cloudwatch_log_group" "firehose_analytics" {
  name              = "/aws/kinesisfirehose/${local.name_prefix}-analytics"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_stream" "firehose_analytics" {
  name           = "DestinationDelivery"
  log_group_name = aws_cloudwatch_log_group.firehose_analytics.name
}

resource "aws_iam_role_policy" "firehose_analytics" {
  name = "firehose-analytics"
  role = aws_iam_role.firehose_analytics.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
          "s3:PutObject",
        ]
        Resource = [
          aws_s3_bucket.analytics.arn,
          "${aws_s3_bucket.analytics.arn}/*",
        ]
      },
      {
        # needed to read the Glue table's schema for the Parquet conversion
        Effect = "Allow"
        Action = ["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${aws_glue_catalog_database.analytics.name}",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.analytics.name}/${aws_glue_catalog_table.events.name}",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.firehose_analytics.arn}:*"
      }
    ]
  })
}

resource "aws_kinesis_firehose_delivery_stream" "analytics" {
  name        = "${local.name_prefix}-analytics"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose_analytics.arn
    bucket_arn          = aws_s3_bucket.analytics.arn
    prefix              = "events/"
    error_output_prefix = "errors/!{firehose:error-output-type}/"
    buffering_size      = 1
    buffering_interval  = 60 # portfolio traffic -- small buffer keeps Athena results fresh for the demo

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose_analytics.name
      log_stream_name = aws_cloudwatch_log_stream.firehose_analytics.name
    }

    data_format_conversion_configuration {
      enabled = true

      input_format_configuration {
        deserializer {
          open_x_json_ser_de {
            column_to_json_key_mappings = {
              detail_type = "detail-type"
            }
          }
        }
      }

      output_format_configuration {
        serializer {
          parquet_ser_de {}
        }
      }

      schema_configuration {
        database_name = aws_glue_catalog_database.analytics.name
        table_name    = aws_glue_catalog_table.events.name
        role_arn      = aws_iam_role.firehose_analytics.arn
      }
    }
  }
}

resource "aws_cloudwatch_event_rule" "pipeline_events" {
  name        = "${local.name_prefix}-pipeline-events"
  description = "montage-pipeline job.* lifecycle events -> analytics Firehose"

  # must match services/common/events.py's SOURCE constant
  event_pattern = jsonencode({
    source = ["montage.pipeline"]
  })
}

data "aws_iam_policy_document" "eventbridge_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eventbridge_firehose" {
  name               = "${local.name_prefix}-eventbridge-firehose"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_assume.json
}

resource "aws_iam_role_policy" "eventbridge_firehose" {
  name = "eventbridge-firehose"
  role = aws_iam_role.eventbridge_firehose.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["firehose:PutRecord", "firehose:PutRecordBatch"]
        Resource = aws_kinesis_firehose_delivery_stream.analytics.arn
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "pipeline_events_firehose" {
  rule      = aws_cloudwatch_event_rule.pipeline_events.name
  target_id = "analytics-firehose"
  arn       = aws_kinesis_firehose_delivery_stream.analytics.arn
  role_arn  = aws_iam_role.eventbridge_firehose.arn
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
