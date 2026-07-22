# dev-only convenience -- bypasses the HeadObject/size re-check a real
# start endpoint would do, must stay off outside dev
resource "aws_cloudwatch_event_rule" "raw_upload" {
  count       = var.enable_dev_eventbridge_trigger ? 1 : 0
  name        = "${local.name_prefix}-raw-object-created"
  description = "S3 ObjectCreated on raw/ -> trigger Lambda"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.this["raw"].bucket] }
      object = { key = [{ prefix = "raw/" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "trigger_lambda" {
  count     = var.enable_dev_eventbridge_trigger ? 1 : 0
  rule      = aws_cloudwatch_event_rule.raw_upload[0].name
  target_id = "trigger-lambda"
  arn       = aws_lambda_function.trigger.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  count         = var.enable_dev_eventbridge_trigger ? 1 : 0
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.trigger.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.raw_upload[0].arn
}
