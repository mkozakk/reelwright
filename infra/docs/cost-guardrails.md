# Cost & Operational Guardrails (`budget.tf`, `alerts.tf`, `dashboard.tf`)

## Budget

One AWS Budget (`var.monthly_budget_usd`, default $10), with three notifications: 80% actual, 100% actual, and 100% forecasted, all routed to one SNS topic.

## Alerts

`alerts.tf` defines that SNS topic plus one email subscription (`var.budget_alert_email`), the single fan-in point for the budget notifications above and the two `sfn.tf` alarms, DLQ depth and execution failures.

## Dashboard

One CloudWatch dashboard: `AWS/States` executions-by-outcome and execution time, per-Lambda `Duration` across all twelve pipeline functions, DLQ depth, and an error-rate-percent math expression. Alongside it, explicit 14-day-retention log groups for every pipeline Lambda, since Lambda auto-creates `/aws/lambda/<name>` with **no** retention on first invoke otherwise: the classic silent bill creeper.

## Related

- [[orchestration]]: the state machine metrics this dashboard visualizes
- [[analytics]]: per-job cost tracking, distinct from this file's infrastructure-level budget
