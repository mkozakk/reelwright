locals {
  pipeline_lambda_functions = [
    aws_lambda_function.trigger.function_name,
    aws_lambda_function.probe.function_name,
    aws_lambda_function.analyze_loudness.function_name,
    aws_lambda_function.analyze_scenes.function_name,
    aws_lambda_function.analyze_transcribe.function_name,
    aws_lambda_function.plan.function_name,
    aws_lambda_function.cut_prepare.function_name,
    aws_lambda_function.cut.function_name,
    aws_lambda_function.semaphore_acquire.function_name,
    aws_lambda_function.semaphore_release.function_name,
    aws_lambda_function.finish.function_name,
    aws_lambda_function.job_api.function_name,
  ]
}

# Lambda auto-creates /aws/lambda/<name> with no retention on first invoke --
# the classic silent bill creeper (docs/DESIGN.md §9). Matches the 14-day
# retention already set on the render/sfn/job_api log groups.
resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(local.pipeline_lambda_functions)
  name              = "/aws/lambda/${each.value}"
  retention_in_days = 14
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${local.name_prefix}-pipeline"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Jobs by outcome"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", aws_sfn_state_machine.pipeline.arn],
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.pipeline.arn],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.pipeline.arn],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title   = "Pipeline execution time"
          view    = "timeSeries"
          region  = var.aws_region
          stat    = "Average"
          period  = 300
          metrics = [["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.pipeline.arn]]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 24, height = 6
        properties = {
          title   = "Step durations (per-Lambda Duration)"
          view    = "timeSeries"
          region  = var.aws_region
          stat    = "Average"
          period  = 300
          metrics = [for fn in local.pipeline_lambda_functions : ["AWS/Lambda", "Duration", "FunctionName", fn]]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = {
          title   = "DLQ depth"
          view    = "timeSeries"
          region  = var.aws_region
          stat    = "Maximum"
          period  = 300
          metrics = [["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.dlq.name]]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6
        properties = {
          title  = "Error rate %"
          view   = "timeSeries"
          region = var.aws_region
          period = 300
          metrics = [
            [{ expression = "100*(m2/m1)", label = "error rate %", id = "e1" }],
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", aws_sfn_state_machine.pipeline.arn, { id = "m1", visible = false }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.pipeline.arn, { id = "m2", visible = false }],
          ]
        }
      },
    ]
  })
}
