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
