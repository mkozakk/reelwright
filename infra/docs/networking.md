# Networking (`vpc.tf`)

One VPC, two public and two private subnets across two availability zones, no NAT gateway.

## Public subnets

Exist for exactly one thing: the Fargate render task needs a public IP to pull its image from ECR. With no NAT gateway, a public IP on the task itself is the only way out to the internet.

## Private subnets

Carry no internet route at all. `probe`, `cut`, and the two analysis Lambdas that touch raw, attacker-controlled media (`analyze_scenes`, `analyze_transcribe`) run here, egress-limited to two gateway VPC endpoints (S3, DynamoDB, both free and requiring no ENI). Anything these Lambdas might need beyond S3 and DynamoDB simply isn't reachable from inside the subnet.

## Security groups

Two, matching the two network postures above:

- `isolated_lambda`: 443 to the S3 and DynamoDB managed prefix lists only.
- `render_task`: 443 to the S3 prefix list, plus 443 to `0.0.0.0/0` for the ECR pull, since no managed prefix list exists for ECR.

## Related

- [[compute]]: the Lambdas and Fargate task this network layout constrains
- [[storage]]: the S3/DynamoDB endpoints private-subnet functions reach through
