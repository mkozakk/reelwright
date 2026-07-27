# trigger/semaphore need only services/common + boto3 (already in the Lambda
# runtime), no ffmpeg -- a container image would be pure overhead here.
# Built by scripts/build_lambda_zips.sh before `terraform apply`.
locals {
  trigger_zip   = "${path.module}/build/trigger.zip"
  semaphore_zip = "${path.module}/build/semaphore.zip"
}

resource "aws_lambda_function" "trigger" {
  function_name    = "${local.name_prefix}-trigger"
  role             = aws_iam_role.trigger.arn
  runtime          = "python3.12"
  handler          = "services.trigger.handler.handler"
  filename         = local.trigger_zip
  source_code_hash = filebase64sha256(local.trigger_zip)
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      JOBS_TABLE        = aws_dynamodb_table.jobs.name
      STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn
    }
  }
}

resource "aws_lambda_function" "semaphore_acquire" {
  function_name    = "${local.name_prefix}-semaphore-acquire"
  role             = aws_iam_role.semaphore.arn
  runtime          = "python3.12"
  handler          = "services.semaphore.handler.acquire_handler"
  filename         = local.semaphore_zip
  source_code_hash = filebase64sha256(local.semaphore_zip)
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      JOBS_TABLE             = aws_dynamodb_table.jobs.name
      RENDER_CONCURRENCY_CAP = tostring(var.render_concurrency_cap)
    }
  }
}

resource "aws_lambda_function" "semaphore_release" {
  function_name    = "${local.name_prefix}-semaphore-release"
  role             = aws_iam_role.semaphore.arn
  runtime          = "python3.12"
  handler          = "services.semaphore.handler.release_handler"
  filename         = local.semaphore_zip
  source_code_hash = filebase64sha256(local.semaphore_zip)
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      JOBS_TABLE = aws_dynamodb_table.jobs.name
    }
  }
}

# Pinned via the ECR image digest, not the mutable tag -- a re-push under the
# same tag is then picked up automatically on the next `terraform apply`.
# Requires scripts/build_and_push_images.sh to have pushed var.image_tag first.
data "aws_ecr_image" "lambda" {
  repository_name = aws_ecr_repository.lambda.name
  image_tag       = var.image_tag
}

locals {
  lambda_image_uri = "${aws_ecr_repository.lambda.repository_url}@${data.aws_ecr_image.lambda.image_digest}"
}

resource "aws_lambda_function" "probe" {
  function_name = "${local.name_prefix}-probe"
  role          = aws_iam_role.probe.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 120
  memory_size   = 1024

  image_config {
    command = ["services.probe.handler.handler"]
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.isolated_lambda.id]
  }

  environment {
    variables = {
      JOBS_TABLE  = aws_dynamodb_table.jobs.name
      RAW_BUCKET  = aws_s3_bucket.this["raw"].bucket
      WORK_BUCKET = aws_s3_bucket.this["work"].bucket
    }
  }
}

resource "aws_lambda_function" "cut_prepare" {
  function_name = "${local.name_prefix}-cut-prepare"
  role          = aws_iam_role.cut.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 30
  memory_size   = 256

  image_config {
    command = ["services.cut.prepare.handler"]
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.isolated_lambda.id]
  }

  environment {
    variables = {
      JOBS_TABLE = aws_dynamodb_table.jobs.name
    }
  }
}

resource "aws_lambda_function" "cut" {
  function_name = "${local.name_prefix}-cut"
  role          = aws_iam_role.cut.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 300
  memory_size   = 2048

  image_config {
    command = ["services.cut.handler.handler"]
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.isolated_lambda.id]
  }

  environment {
    variables = {
      JOBS_TABLE  = aws_dynamodb_table.jobs.name
      RAW_BUCKET  = aws_s3_bucket.this["raw"].bucket
      WORK_BUCKET = aws_s3_bucket.this["work"].bucket
    }
  }
}

resource "aws_lambda_function" "finish" {
  function_name = "${local.name_prefix}-finish"
  role          = aws_iam_role.finish.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 60
  memory_size   = 512

  image_config {
    command = ["services.finish.handler.handler"]
  }

  environment {
    variables = {
      JOBS_TABLE                 = aws_dynamodb_table.jobs.name
      OUTPUT_BUCKET              = aws_s3_bucket.this["output"].bucket
      CLOUDFRONT_DOMAIN          = aws_cloudfront_distribution.output.domain_name
      CLOUDFRONT_KEY_PAIR_ID     = aws_cloudfront_public_key.signing.id
      CLOUDFRONT_PRIVATE_KEY_PEM = tls_private_key.cloudfront_signing.private_key_pem
    }
  }
}
