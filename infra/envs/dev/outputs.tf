output "raw_bucket" {
  value = aws_s3_bucket.this["raw"].bucket
}

output "work_bucket" {
  value = aws_s3_bucket.this["work"].bucket
}

output "output_bucket" {
  value = aws_s3_bucket.this["output"].bucket
}

output "jobs_table" {
  value = aws_dynamodb_table.jobs.name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "cloudfront_domain" {
  value = aws_cloudfront_distribution.output.domain_name
}

output "ecr_lambda_repository_url" {
  value = aws_ecr_repository.lambda.repository_url
}

output "ecr_render_task_repository_url" {
  value = aws_ecr_repository.render_task.repository_url
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}
