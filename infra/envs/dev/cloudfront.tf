# an OAC-enforcing bucket policy breaks S3 presigned GETs, so CloudFront
# signed URLs are the one path for both playback and download
resource "tls_private_key" "cloudfront_signing" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "aws_cloudfront_public_key" "signing" {
  name        = "${local.name_prefix}-signing-key"
  encoded_key = tls_private_key.cloudfront_signing.public_key_pem
}

resource "aws_cloudfront_key_group" "signing" {
  name  = "${local.name_prefix}-signing-key-group"
  items = [aws_cloudfront_public_key.signing.id]
}

resource "aws_cloudfront_origin_access_control" "output" {
  name                              = "${local.name_prefix}-output-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "output" {
  enabled             = true
  comment             = "${local.name_prefix} output bucket -- playback + download"
  price_class         = "PriceClass_100"
  default_root_object = ""

  origin {
    domain_name              = aws_s3_bucket.this["output"].bucket_regional_domain_name
    origin_id                = "output-bucket"
    origin_access_control_id = aws_cloudfront_origin_access_control.output.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "output-bucket"
    viewer_protocol_policy = "redirect-to-https"
    trusted_key_groups     = [aws_cloudfront_key_group.signing.id]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_s3_bucket_policy" "output_cloudfront" {
  bucket = aws_s3_bucket.this["output"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.this["output"].arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.output.arn }
      }
    }]
  })
}
