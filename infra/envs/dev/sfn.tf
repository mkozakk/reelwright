resource "aws_sqs_queue" "dlq" {
  name                      = "${local.name_prefix}-pipeline-dlq"
  message_retention_seconds = 1209600 # 14 days
}

# an unwatched DLQ is a black hole
resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${local.name_prefix}-dlq-depth"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "montage-pipeline job(s) landed in the DLQ"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.name_prefix}-pipeline"
  retention_in_days = 14
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.name_prefix}-pipeline"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/statemachine.asl.json.tpl", {
    probe_arn                  = aws_lambda_function.probe.arn
    cut_prepare_arn            = aws_lambda_function.cut_prepare.arn
    cut_arn                    = aws_lambda_function.cut.arn
    semaphore_acquire_arn      = aws_lambda_function.semaphore_acquire.arn
    semaphore_release_arn      = aws_lambda_function.semaphore_release.arn
    finish_arn                 = aws_lambda_function.finish.arn
    ecs_cluster_arn            = aws_ecs_cluster.main.arn
    render_task_definition_arn = aws_ecs_task_definition.render.arn
    render_task_sg_id          = aws_security_group.render_task.id
    public_subnet_ids_json     = jsonencode(aws_subnet.public[*].id)
    jobs_table_name            = aws_dynamodb_table.jobs.name
    dlq_url                    = aws_sqs_queue.dlq.url
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }
}
