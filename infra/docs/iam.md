# IAM & OIDC (`iam.tf`, `oidc.tf`)

## Per-function least privilege

`iam.tf` is one role plus an inline least-privilege policy **per Lambda/task**, with no shared "pipeline execution role." Each policy is scoped to exactly what that function touches. `cut`'s role, for example, gets `dynamodb:GetItem`/`UpdateItem` on the jobs table, `s3:GetObject` on the raw bucket, and `s3:GetObject`/`PutObject` (plus `ListBucket`, needed because S3 returns 403 rather than 404 on a HeadObject against a nonexistent key without it) on the work bucket, and nothing else.

## The state machine's own role

`aws_iam_role.sfn` is the one role with broad reach, since it's what invokes every Lambda and runs the ECS task: `lambda:InvokeFunction` scoped to exactly the twelve pipeline function ARNs (not `*`), `ecs:RunTask` scoped to the render task definition family, `iam:PassRole` scoped to the two render-task roles, plus the fixed grants Step Functions itself needs for ECS callback events, EventBridge `PutEvents` (the `JobFailed` native integration), DLQ `SendMessage`, and its own CloudWatch Logs delivery.

## GitHub Actions federation (`oidc.tf`)

No long-lived AWS keys in repo secrets, ever. Two roles:

- **`github_actions`** (plan/build): `ReadOnlyAccess` plus three narrow additions: S3 lock-object `PutObject`/`DeleteObject` (the S3-native state lock writes a `.tflock` marker even for `plan`, which `ReadOnlyAccess` alone would block), `bedrock:ListTagsForResource` on the guardrail (`ReadOnlyAccess` covers `Get*`/`List*` generally but not tag reads, which Terraform's refresh calls on every `plan`), and ECR push scoped to the three repos. Its OIDC trust condition matches on GitHub's `sub` claim wildcarded past the numeric owner/repo ids, since this repo has been renamed and GitHub permanently qualifies `sub` with those ids once a rename has happened, so a plain `repo:owner/repo:*` match would silently never fire.
- **`github_actions_deploy`** (apply): `PowerUserAccess` plus `IAMFullAccess`, since Terraform apply here creates IAM roles and policies, which `PowerUserAccess` deliberately excludes. Its trust condition additionally requires `environment:dev` in the `sub` claim, meaning it can only be assumed from a workflow run that passed the GitHub Environment's required-reviewer approval gate. That human approval is what actually grants write access, not anything in this policy.

## Related

- [[compute]]: the functions each per-service role in this file is scoped to
- [[deploy]]: the workflows that assume the two OIDC roles this file defines
