# CI gets read-only + ECR push only -- `terraform apply` stays a manual,
# local step run by the account owner.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS validates well-known OIDC providers against its own trusted CA store;
  # this is the root CA fingerprint backing GitHub's cert. Re-fetch with
  # `openssl s_client` if it ever needs rotating.
  thumbprint_list = ["ab9d0263244dd0326eb67015705a667e79cfe998"]
}

data "aws_iam_policy_document" "github_actions_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # AWS requires an OIDC trust policy to condition on `sub` or
    # `job_workflow_ref` -- it rejects one scoped only by other claims. GitHub
    # also permanently qualifies `sub` as `repo:owner@ownerId/repo@repoId:...`
    # once a repo or owner has ever been renamed (ours has), so a plain
    # `repo:owner/repo:*` StringLike match silently never fires. Wildcard the
    # id segments instead of hardcoding the numeric ids.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.github_owner}@*/${local.github_repo_name}@*:*"]
    }

    # belt-and-suspenders: `repository` always holds the current owner/repo
    # name regardless of rename history, so pin it too.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:repository"
      values   = [var.github_repository]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${local.name_prefix}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume.json
}

resource "aws_iam_role_policy_attachment" "github_actions_read_only" {
  role       = aws_iam_role.github_actions.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# ReadOnlyAccess blocks s3:PutObject, but the S3-native state lock (backend's
# use_lockfile = true) writes/deletes a `.tflock` marker even for `plan` --
# grant just that, scoped to this env's lock object.
resource "aws_iam_role_policy" "github_actions_tfstate_lock" {
  name = "tfstate-lock"
  role = aws_iam_role.github_actions.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = "arn:aws:s3:::reelwright-tfstate-386566550838/envs/dev/terraform.tfstate.tflock"
      }
    ]
  })
}

resource "aws_iam_role_policy" "github_actions_ecr_push" {
  name = "ecr-push"
  role = aws_iam_role.github_actions.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
        ]
        Resource = [
          aws_ecr_repository.lambda.arn,
          aws_ecr_repository.render_task.arn,
          aws_ecr_repository.analyze_transcribe.arn,
        ]
      }
    ]
  })
}

# separate, more-powerful role for `terraform apply` from CI. Its trust is
# locked to this repo AND the protected GitHub Environment, so the
# environment's required-reviewer approval is literally what grants write
# access -- the plan/build role above stays read-only. Broad managed policies
# are acceptable here precisely because nothing can assume this role without
# passing the human approval gate.
data "aws_iam_policy_document" "github_deploy_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # see the comment on github_actions_assume above: sub is wildcarded past
    # the owner/repo numeric ids GitHub adds post-rename, then `repository`
    # and `environment` re-tighten it back down to this repo and this env.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.github_owner}@*/${local.github_repo_name}@*:environment:${var.environment}"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:repository"
      values   = [var.github_repository]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:environment"
      values   = [var.environment]
    }
  }
}

resource "aws_iam_role" "github_actions_deploy" {
  name               = "${local.name_prefix}-github-actions-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume.json
}

resource "aws_iam_role_policy_attachment" "github_deploy_power" {
  role       = aws_iam_role.github_actions_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

# PowerUserAccess deliberately excludes IAM writes; terraform apply here
# creates roles/policies, so IAM management is granted on top.
resource "aws_iam_role_policy_attachment" "github_deploy_iam" {
  role       = aws_iam_role.github_actions_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/IAMFullAccess"
}
