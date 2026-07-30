# HTTP API in front of the job_api Lambda. CORS is owned by the Lambda (it
# emits the headers and answers OPTIONS itself, and is unit-tested that way), so
# no cors_configuration here -- setting both would double the Allow-Origin
# header and browsers reject that.
resource "aws_apigatewayv2_api" "job_api" {
  name          = "${local.name_prefix}-job-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "job_api" {
  api_id                 = aws_apigatewayv2_api.job_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.job_api.invoke_arn
  payload_format_version = "2.0"
}

# validates the Cognito ID token (docs/phases/phase-7.md) -- API Gateway
# checks the signature/issuer/audience/expiry and hands the claims to the
# Lambda in event.requestContext.authorizer.jwt.claims; no token verification
# code lives in services/job_api itself.
resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.job_api.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${local.name_prefix}-cognito-jwt"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.frontend.id]
    issuer   = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${aws_cognito_user_pool.users.id}"
  }
}

locals {
  # OPTIONS stays unauthenticated -- it's the CORS preflight, answered by the
  # Lambda itself (comment above), and preflight requests never carry an
  # Authorization header.
  job_api_routes = {
    "POST /jobs"               = true
    "POST /jobs/{id}/complete" = true
    "POST /jobs/{id}/rerender" = true
    "GET /jobs/{id}"           = true
    "GET /jobs"                = true
    "OPTIONS /{proxy+}"        = false
  }
}

resource "aws_apigatewayv2_route" "job_api" {
  for_each  = local.job_api_routes
  api_id    = aws_apigatewayv2_api.job_api.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.job_api.id}"

  authorization_type = each.value ? "JWT" : "NONE"
  authorizer_id      = each.value ? aws_apigatewayv2_authorizer.cognito.id : null
}

resource "aws_cloudwatch_log_group" "job_api" {
  name              = "/aws/apigateway/${local.name_prefix}-job-api"
  retention_in_days = 14
}

# throttling is the coarse denial-of-wallet guard at the edge; the per-IP daily
# cap (app-level DynamoDB counter in services/job_api) is the finer one, since
# API Gateway has no per-IP quotas (docs/DESIGN.md 10)
resource "aws_apigatewayv2_stage" "job_api" {
  api_id      = aws_apigatewayv2_api.job_api.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate_limit
    throttling_burst_limit = var.api_throttle_burst_limit
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.job_api.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
    })
  }
}

resource "aws_lambda_permission" "job_api" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.job_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.job_api.execution_arn}/*/*"
}
