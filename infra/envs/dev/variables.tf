variable "aws_region" {
  type    = string
  default = "eu-north-1"
}

variable "project_name" {
  type    = string
  default = "reelwright"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "budget_alert_email" {
  description = "Recipient for the monthly AWS Budget alarm (docs/phases/phase-2.md)"
  type        = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
}

variable "render_concurrency_cap" {
  description = "DynamoDB semaphore cap on concurrent Fargate render tasks (services/common/semaphore.py)"
  type        = number
  default     = 2
}

variable "image_tag" {
  description = "Tag pushed by scripts/build_and_push_images.sh before the full apply"
  type        = string
  default     = "latest"
}

variable "github_repository" {
  description = "owner/repo allowed to assume the CI deploy role via GitHub OIDC"
  type        = string
  default     = "mkozakk/reelwright"
}

variable "enable_dev_eventbridge_trigger" {
  description = "Single-file S3-upload convenience trigger, dev-only per docs/DESIGN.md §12 -- must be off outside dev"
  type        = bool
  default     = true
}

variable "bedrock_region" {
  description = "Region for Bedrock planning calls and the Guardrail; Nova Lite must be available there (the stack region eu-north-1 has no Nova, so the plan Lambda calls cross-region -- docs/phases/phase-4.md)"
  type        = string
  default     = "us-east-1"
}

variable "nova_model_id" {
  description = "Bedrock model id for the planner -- config, not code (docs/DESIGN.md §4)"
  type        = string
  default     = "amazon.nova-lite-v1:0"
}

variable "plan_max_output_tokens" {
  description = "Per-job Bedrock output-token cap on the planning call (denial-of-wallet, docs/DESIGN.md §10 layer 6)"
  type        = number
  default     = 2000
}

variable "frontend_origin" {
  description = "Allowed browser origin for the job API and raw-bucket upload CORS; '*' in dev, the CloudFront site origin once the frontend has a domain (docs/phases/phase-5.md)"
  type        = string
  default     = "*"
}

variable "api_throttle_rate_limit" {
  description = "Steady-state requests/sec across the job API (edge denial-of-wallet guard, docs/DESIGN.md §10)"
  type        = number
  default     = 20
}

variable "api_throttle_burst_limit" {
  description = "Burst request ceiling across the job API"
  type        = number
  default     = 10
}

variable "cognito_callback_urls" {
  description = "OAuth callback/logout URLs for the Cognito Hosted UI client -- local dev by default, the frontend CloudFront domain is added once Stage F's distribution exists (docs/phases/phase-7.md)"
  type        = list(string)
  default     = ["http://localhost:8000/callback.html"]
}
