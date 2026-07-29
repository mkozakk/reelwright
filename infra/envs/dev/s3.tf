locals {
  # raw is nominally "48h" -- S3 lifecycle only supports whole-day granularity
  buckets = {
    raw    = { suffix = "raw", expire_days = 2 }
    work   = { suffix = "work", expire_days = 7 }
    output = { suffix = "output", expire_days = 30 }
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets
  bucket   = "${local.name_prefix}-${each.value.suffix}-${data.aws_caller_identity.current.account_id}"
  tags     = { Name = "${local.name_prefix}-${each.value.suffix}" }

  force_destroy = true # disposable dev infra -- destroy must not block on leftover job objects
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  rule {
    id     = "expire-objects"
    status = "Enabled"
    filter {}
    expiration {
      days = each.value.expire_days
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
  }
}

resource "aws_s3_bucket_notification" "raw" {
  bucket      = aws_s3_bucket.this["raw"].id
  eventbridge = true
}

# lets the browser PUT straight to the presigned upload URL; the presign already
# binds key + content-type/length, so this only governs which origins may send it
resource "aws_s3_bucket_cors_configuration" "raw" {
  bucket = aws_s3_bucket.this["raw"].id
  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = [var.frontend_origin]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}
