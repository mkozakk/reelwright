# Bootstrap (`bootstrap/`)

Solves the chicken-and-egg problem of remote state: `envs/dev`'s backend is an S3 bucket, and that bucket has to exist and be addressable *before* `envs/dev` can be initialized against it. `bootstrap/` is a second, tiny root module with its own local state, never migrated to S3, since it's the thing that creates the S3 backend in the first place, applied once, by hand, before `envs/dev` is ever touched.

## What it creates

One resource family: an S3 bucket (`reelwright-tfstate-<account_id>`) with versioning, AES256 default encryption, a public-access block, a deny-insecure-transport bucket policy, and a 90-day noncurrent-version expiration.

## Wiring it into `envs/dev`

`envs/dev/versions.tf`'s `backend "s3"` block hardcodes this bucket name, since Terraform backend blocks can't interpolate variables or reference another module's output. The bootstrap output gets copy-pasted once after the first `bootstrap` apply, and never touched again unless the backend itself needs to move.

`use_lockfile = true` uses S3's own conditional-write locking, so there's no separate DynamoDB lock table to provision or pay for.

## Related

- [[storage]]: `envs/dev`'s own buckets, provisioned once this module's bucket already exists
