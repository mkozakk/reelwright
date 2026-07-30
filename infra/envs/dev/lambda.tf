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

  tracing_config {
    mode = "Active"
  }

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

  tracing_config {
    mode = "Active"
  }

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

  tracing_config {
    mode = "Active"
  }

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

data "aws_ecr_image" "analyze_transcribe" {
  repository_name = aws_ecr_repository.analyze_transcribe.name
  image_tag       = var.image_tag
}

locals {
  lambda_image_uri             = "${aws_ecr_repository.lambda.repository_url}@${data.aws_ecr_image.lambda.image_digest}"
  analyze_transcribe_image_uri = "${aws_ecr_repository.analyze_transcribe.repository_url}@${data.aws_ecr_image.analyze_transcribe.image_digest}"
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

  tracing_config {
    mode = "Active"
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

resource "aws_lambda_function" "analyze_loudness" {
  function_name = "${local.name_prefix}-analyze-loudness"
  role          = aws_iam_role.analyze_loudness.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 60
  memory_size   = 512

  image_config {
    command = ["services.analyze_loudness.handler.handler"]
  }

  tracing_config {
    mode = "Active"
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.isolated_lambda.id]
  }

  environment {
    variables = {
      JOBS_TABLE  = aws_dynamodb_table.jobs.name
      WORK_BUCKET = aws_s3_bucket.this["work"].bucket
    }
  }
}

# decodes raw, attacker-controlled video bytes directly (scdet needs frames,
# the FLAC Probe already extracts is audio-only) -- same no-egress VPC
# posture as probe/cut
resource "aws_lambda_function" "analyze_scenes" {
  function_name = "${local.name_prefix}-analyze-scenes"
  role          = aws_iam_role.analyze_scenes.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 120
  memory_size   = 1024

  image_config {
    command = ["services.analyze_scenes.handler.handler"]
  }

  tracing_config {
    mode = "Active"
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

# own image: faster-whisper + baked model weights, irrelevant to every
# other Lambda's cold start. Architecture pinned x86_64 pending an
# arm64-vs-x86_64 benchmark -- see docker/transcribe.Dockerfile.
# memory_size is 3008, not a rounder 4096, because this account's
# per-function CreateFunction ceiling is 3008 MB (not the usual 10240) --
# an account-level restriction, not a sizing decision. faster-whisper
# `small` int8 still fits under 3 GB.
resource "aws_lambda_function" "analyze_transcribe" {
  function_name = "${local.name_prefix}-analyze-transcribe"
  role          = aws_iam_role.analyze_transcribe.arn
  package_type  = "Image"
  image_uri     = local.analyze_transcribe_image_uri
  architectures = ["x86_64"]
  timeout       = 300
  memory_size   = 3008

  image_config {
    command = ["services.analyze_transcribe.handler.handler"]
  }

  tracing_config {
    mode = "Active"
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.isolated_lambda.id]
  }

  environment {
    variables = {
      JOBS_TABLE  = aws_dynamodb_table.jobs.name
      WORK_BUCKET = aws_s3_bucket.this["work"].bucket
      MODEL_DIR   = "/opt/whisper-model"
    }
  }
}

# no vpc_config -- only touches S3 JSON + DynamoDB, matching finish
resource "aws_lambda_function" "plan" {
  function_name = "${local.name_prefix}-plan"
  role          = aws_iam_role.plan.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 60 # Bedrock round-trip plus one validation retry
  memory_size   = 512

  image_config {
    command = ["services.plan.handler.handler"]
  }

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      JOBS_TABLE             = aws_dynamodb_table.jobs.name
      WORK_BUCKET            = aws_s3_bucket.this["work"].bucket
      NOVA_MODEL_ID          = var.nova_model_id
      BEDROCK_REGION         = var.bedrock_region
      PLAN_MAX_OUTPUT_TOKENS = tostring(var.plan_max_output_tokens)
      GUARDRAIL_ID           = aws_bedrock_guardrail.planning.guardrail_id
      GUARDRAIL_VERSION      = aws_bedrock_guardrail_version.planning.version
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

  tracing_config {
    mode = "Active"
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

  tracing_config {
    mode = "Active"
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

# public-facing job API: create job + presign upload, get status + sign
# playback URLs. No vpc_config -- presigning and CloudFront signing are local
# credential operations, and it only touches DynamoDB + the raw bucket's
# presign path, never raw bytes. Same container image as the rest.
resource "aws_lambda_function" "job_api" {
  function_name = "${local.name_prefix}-job-api"
  role          = aws_iam_role.job_api.arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  timeout       = 15
  memory_size   = 256

  image_config {
    command = ["services.job_api.handler.handler"]
  }

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      JOBS_TABLE                 = aws_dynamodb_table.jobs.name
      RAW_BUCKET                 = aws_s3_bucket.this["raw"].bucket
      CLOUDFRONT_DOMAIN          = aws_cloudfront_distribution.output.domain_name
      CLOUDFRONT_KEY_PAIR_ID     = aws_cloudfront_public_key.signing.id
      CLOUDFRONT_PRIVATE_KEY_PEM = tls_private_key.cloudfront_signing.private_key_pem
      CORS_ORIGIN                = var.frontend_origin
      STATE_MACHINE_ARN          = aws_sfn_state_machine.pipeline.arn
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

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      JOBS_TABLE                 = aws_dynamodb_table.jobs.name
      OUTPUT_BUCKET              = aws_s3_bucket.this["output"].bucket
      CLOUDFRONT_DOMAIN          = aws_cloudfront_distribution.output.domain_name
      CLOUDFRONT_KEY_PAIR_ID     = aws_cloudfront_public_key.signing.id
      CLOUDFRONT_PRIVATE_KEY_PEM = tls_private_key.cloudfront_signing.private_key_pem
      SES_FROM_EMAIL             = var.ses_from_email
    }
  }
}
