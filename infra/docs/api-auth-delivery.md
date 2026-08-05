# API, Auth & Delivery (`apigateway.tf`, `cognito.tf`, `cloudfront.tf`, `frontend.tf`)

## API Gateway

One HTTP API (`AWS_PROXY` to `job_api`, payload format 2.0). CORS headers come from the Lambda itself, since it answers `OPTIONS` directly and is unit-tested that way, so there's no `cors_configuration` block here: setting both would double the `Allow-Origin` header and browsers reject that.

A JWT authorizer validates the Cognito ID token on every route except the `OPTIONS` preflight. API Gateway checks signature, issuer, audience, and expiry itself; `services/job_api` never verifies a token, it just reads `event.requestContext.authorizer.jwt.claims`.

Stage-level throttling (`api_throttle_rate_limit`/`burst_limit`) is the coarse edge denial-of-wallet guard, while the per-IP/per-user DynamoDB daily caps in `job_api/logic.py` are the finer one, since API Gateway has no native per-IP quota.

## Cognito

One user pool (email as username, email auto-verified), one hosted-UI domain, one public app client with no secret, since it runs in the browser, using Authorization Code plus PKCE, with short-lived tokens (60 min id/access, 30 days refresh).

## CloudFront

Two distributions, following the same pattern: Origin Access Control, `PriceClass_100`, AWS-managed `CachingOptimized` policy.

- The `output` distribution serves signed playback and download URLs only (`trusted_key_groups` on the default cache behavior), using a canned-policy RSA key pair that Terraform itself generates via the `tls` provider, since an OAC-enforcing bucket policy breaks plain S3 presigned GETs.
- The `frontend` distribution serves the static site, with both 403 and 404 rewritten to `index.html` for client-side routing.

## Frontend deploy glue

`local_file.frontend_config` renders `frontend_config.js.tpl` (API base URL, Cognito domain, Cognito client id) straight to `frontend/config.js` on every `apply`. That file is git-ignored precisely because it's a Terraform output, not a hand-authored asset. `deploy.yml` syncs `frontend/` to the frontend bucket *after* `apply`, specifically because this file doesn't exist until `apply` produces it.

## Related

- [[deploy]]: the workflow that runs `apply` and syncs the frontend, in that order
- [[job-api]] (services): the Lambda this API Gateway proxies to, and the one that reads Cognito claims
