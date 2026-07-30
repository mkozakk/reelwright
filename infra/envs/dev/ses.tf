# single verified "from" identity, not a domain identity -- no Route53
# domain exists in this infra to verify a domain against. Verification is a
# manual step (README) -- Terraform can request it but can't click the link.
resource "aws_ses_email_identity" "from" {
  email = var.ses_from_email
}
