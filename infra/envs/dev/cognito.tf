# Cognito user pool backing the API's JWT authorizer (apigateway.tf) and the
# frontend's sign-in flow (Hosted UI, Authorization Code + PKCE -- a public
# client with no secret, since it runs in the browser).
resource "aws_cognito_user_pool" "users" {
  name = "${local.name_prefix}-users"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = local.tags
}

resource "aws_cognito_user_pool_domain" "users" {
  domain       = "${local.name_prefix}-auth"
  user_pool_id = aws_cognito_user_pool.users.id
}

# callback/logout URLs cover both local dev serving of frontend/
# (var.cognito_callback_urls) and the deployed Stage F CloudFront distribution.
locals {
  cognito_urls = concat(
    var.cognito_callback_urls,
    [
      "https://${aws_cloudfront_distribution.frontend.domain_name}/callback.html",
      "https://${aws_cloudfront_distribution.frontend.domain_name}/index.html",
    ]
  )
}

resource "aws_cognito_user_pool_client" "frontend" {
  name         = "${local.name_prefix}-frontend"
  user_pool_id = aws_cognito_user_pool.users.id

  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = local.cognito_urls
  logout_urls   = local.cognito_urls

  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  id_token_validity      = 60
  access_token_validity  = 60
  refresh_token_validity = 30
  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }
}
