# Managed defense-in-depth on the planning call (docs/DESIGN.md §10 layer 5):
# screens the untrusted inputs (user brief + transcript evidence) for prompt
# attacks and harmful content, and masks PII in the transcript before it
# reaches the model. Schema validation stays the boundary -- this complements,
# never replaces it. Masked placeholders (e.g. {NAME}) can surface in `reason`
# strings; accepted, the UI shows them as-is (docs/DESIGN.md §4).

locals {
  # PII entities anonymised (masked to placeholders), not blocked -- a montage
  # of a birthday party legitimately contains names; masking keeps the plan
  # flowing while stripping the identifier before the model sees it.
  guardrail_pii_entities = [
    "NAME", "EMAIL", "PHONE", "ADDRESS", "AGE",
    "CREDIT_DEBIT_CARD_NUMBER", "US_SOCIAL_SECURITY_NUMBER", "PASSWORD",
  ]
}

resource "aws_bedrock_guardrail" "planning" {
  provider                  = aws.bedrock
  name                      = "${local.name_prefix}-planning"
  description               = "Prompt-attack + harmful-content screening and PII masking on the montage planning call (docs/DESIGN.md §10)."
  blocked_input_messaging   = "This request was blocked by the content policy."
  blocked_outputs_messaging = "The response was blocked by the content policy."

  content_policy_config {
    # PROMPT_ATTACK only applies to input; output_strength must be NONE.
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
  }

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = local.guardrail_pii_entities
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }
}

# A numbered version pins what the Lambda applies; the DRAFT keeps moving as the
# guardrail is edited, a numbered version does not.
resource "aws_bedrock_guardrail_version" "planning" {
  provider      = aws.bedrock
  guardrail_arn = aws_bedrock_guardrail.planning.guardrail_arn
  description   = "Version applied by the plan Lambda."
}
