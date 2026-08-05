# Deploy Flow

Two GitHub Actions workflows, both federating via `oidc.tf`'s roles.

## `ci.yml`

Runs on every push and PR to `main`: installs ffmpeg, runs `pytest -m "not media and not whisper"` then `pytest -m "media and not whisper"`, and `terraform fmt -check -recursive` across `infra/`. It never touches real AWS beyond the read-only `plan` step.

## `deploy.yml`

Runs on push to `main`, or manual dispatch, as three jobs:

1. **`terraform-plan`** assumes `github_actions` and runs `terraform plan` against `envs/dev`.
2. **`build-and-push-images`** builds and pushes the three container images (`lambda`, `render-task`, `analyze-transcribe`) to ECR, tagged `latest` and by commit SHA.
3. **`apply`** is gated behind the `dev` GitHub Environment's required reviewer, assumes `github_actions_deploy`, and runs `terraform apply` (which regenerates `frontend/config.js` via `local_file`), then syncs `frontend/` to the frontend S3 bucket and invalidates its CloudFront distribution. That last step runs after `apply` specifically because `frontend/config.js` doesn't exist until `apply` produces it.

## Why only `apply` is gated

`terraform-plan` and the image build stay ungated so a broken plan or a broken image build surfaces on every push. Only the state-changing `apply` needs a human to click approve, since that's the one job with write access to real infrastructure.

## Related

- [[iam]]: the two OIDC roles these workflows assume
- [[api-auth-delivery]]: the frontend config file `apply` regenerates and `deploy.yml` syncs
