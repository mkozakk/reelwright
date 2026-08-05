# Finish (`finish/`)

The last step in the happy path: verifies the render actually produced something, generates a thumbnail, and signs a playback URL.

## Verification

`run_finish` downloads the rendered output and re-probes it with `renderer.probe.probe_file`. A duration of zero fails the job, since a render that silently produced an empty file must not be allowed to reach `DONE` and show up as a working montage to the user.

## Thumbnail and signed URL

Extracts a thumbnail via `renderer.thumbnail.extract_thumbnail` and signs a CloudFront playback URL before marking the job `DONE` and publishing `job.rendered`.

## Signing from scratch

`cloudfront_sign.py` implements CloudFront's canned-policy URL signing itself, using `cryptography`'s RSA-SHA1 `PKCS1v15` signer over a JSON policy document, base64url-encoded, since no AWS SDK helper for this exists in boto3. `job_api/handler.py` reuses the exact same `cloudfront_sign.sign_url` function for `GET /jobs/{id}`'s playback and thumbnail URLs, so there's one signing implementation in the whole codebase.

## Related

- [[render]]: produces the output this service verifies
- [[job-api]]: shares this service's `cloudfront_sign.sign_url` for status-endpoint URLs
