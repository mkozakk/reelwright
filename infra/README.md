# Infra (`infra/`)

100% Terraform, two independent root modules. There's no reusable `modules/` directory; each `.tf` file in `envs/dev/` owns one resource family and is named for it. This is what actually provisions every Lambda, the Fargate task, the state machine, and the data stores that `services/` reads and writes.

## Directory Structure

```text
infra/
├── docs/               # Full documentation of all infra topics
├── bootstrap/          # one-time, applied manually, local state
│   └── main.tf           # the S3 bucket that becomes envs/dev's remote state backend
└── envs/dev/            # the actual stack, S3 remote state (bootstrap's bucket)
    ├── vpc.tf, s3.tf, dynamodb.tf, ecr.tf
    ├── lambda.tf, ecs.tf, sfn.tf, statemachine.asl.json.tpl
    ├── apigateway.tf, cognito.tf, cloudfront.tf, frontend.tf
    ├── eventbridge.tf, analytics.tf, bedrock.tf
    ├── budget.tf, alerts.tf, dashboard.tf
    ├── iam.tf, oidc.tf
    └── build/                gitignored, scripts/build_lambda_zips.sh output
```

## Key Responsibilities

- **Bootstrap**: a tiny, separately-applied root module that creates the S3 bucket the real stack's remote state lives in, solving the chicken-and-egg problem of Terraform needing a backend before it can manage one.
- **Networking**: a VPC with no NAT gateway, isolating every Lambda that touches raw or attacker-controlled media behind gateway VPC endpoints only.
- **Storage and compute**: three lifecycle-bounded S3 buckets, one DynamoDB table, twelve Lambda functions across two packaging styles, and one Fargate task definition shared by both Spot and on-demand capacity.
- **Orchestration**: the Step Functions state machine, its DLQ, and its CloudWatch alarms.
- **Delivery**: API Gateway with Cognito JWT auth, and two CloudFront distributions for the static frontend and signed media playback.
- **Cost and operational guardrails**: an AWS Budget, CloudWatch alarms fanning into one SNS topic, and a pipeline dashboard.
- **IAM and CI**: one least-privilege role per Lambda/task, and GitHub Actions OIDC federation with a read-only plan role and a human-gated deploy role.

## Features & Internal Documentation

* **[Bootstrap](docs/bootstrap.md)** - the chicken-and-egg remote-state problem, and the local-state module that solves it (`bootstrap/`).
* **[Networking](docs/networking.md)** - the no-NAT VPC, gateway endpoints, and the two security groups (`vpc.tf`).
* **[Storage](docs/storage.md)** - the three S3 buckets, the DynamoDB jobs table, and the three ECR repos (`s3.tf`, `dynamodb.tf`, `ecr.tf`).
* **[Compute](docs/compute.md)** - the twelve Lambda functions, zip vs. container packaging, and the shared Fargate task definition (`lambda.tf`, `ecs.tf`).
* **[Orchestration](docs/orchestration.md)** - the state machine, its DLQ, and the render retry shape (`sfn.tf`, `statemachine.asl.json.tpl`).
* **[API, Auth & Delivery](docs/api-auth-delivery.md)** - API Gateway, Cognito, the two CloudFront distributions, and the frontend deploy glue (`apigateway.tf`, `cognito.tf`, `cloudfront.tf`, `frontend.tf`).
* **[Analysis Triggers & Guardrails](docs/triggers-guardrails.md)** - the dev-only EventBridge trigger and the Bedrock content/PII guardrail (`eventbridge.tf`, `bedrock.tf`).
* **[Analytics](docs/analytics.md)** - the events bucket, Glue catalog, and the three pre-written Athena queries (`analytics.tf`).
* **[Cost & Operational Guardrails](docs/cost-guardrails.md)** - the AWS Budget, alarms, and the pipeline dashboard (`budget.tf`, `alerts.tf`, `dashboard.tf`).
* **[IAM & OIDC](docs/iam.md)** - the per-function least-privilege policies and GitHub Actions federation (`iam.tf`, `oidc.tf`).
* **[Deploy Flow](docs/deploy.md)** - what `ci.yml` and `deploy.yml` actually run, and why the human approval gate sits where it does.

## Development

```bash
cd infra/envs/dev
terraform init
terraform plan
terraform fmt -check -recursive   # what ci.yml runs
```

Real applies are gated behind a human-reviewed GitHub Environment; see [docs/deploy.md](docs/deploy.md) before running `terraform apply` outside CI.

## Tech stack

| Concern | Technology |
|---|---|
| IaC | Terraform (`~> 1.10`), AWS provider `~> 5.60` |
| Compute | Lambda, Fargate/ECS |
| Orchestration | Step Functions |
| Data | DynamoDB, S3 |
| Delivery | API Gateway (HTTP API), CloudFront, Cognito |
| Analytics | Glue, Athena |
| CI/CD | GitHub Actions, OIDC federation |

## Related Components

- **[Services](../services/README.md)**: the Lambda handlers and Fargate task this infra deploys; every environment variable wired up in `lambda.tf`/`ecs.tf` maps to a `os.environ[...]` read in that code.
- **[Renderer](../renderer/README.md)**: has no AWS dependency itself, but its container image (built from `docker/`) is what several Lambdas and the Fargate task actually run.
