from __future__ import annotations

import boto3

from .models import JobRecord, SourceRef

PK = "pk"


def _table(table_name: str):
    return boto3.resource("dynamodb").Table(table_name)


def job_pk(job_id: str) -> str:
    return f"JOB#{job_id}"


def get_job(table_name: str, job_id: str) -> JobRecord:
    item = _table(table_name).get_item(Key={PK: job_pk(job_id)}).get("Item")
    if item is None:
        raise KeyError(f"no job record for '{job_id}'")
    sources = {
        src_id: SourceRef(**ref) for src_id, ref in item.get("sources", {}).items()
    }
    return JobRecord(
        job_id=job_id,
        status=item["status"],
        created_at=item.get("created_at", ""),
        sources=sources,
        prefs=dict(item.get("prefs", {})),
        edit_plan=item.get("edit_plan"),
        analysis_keys=dict(item.get("analysis_keys", {})),
        target_profile=item.get("target_profile"),
        output_key=item.get("output_key"),
        thumbnail_key=item.get("thumbnail_key"),
        error=item.get("error"),
        ttl=item.get("ttl"),
    )


def update_job(table_name: str, job_id: str, **attrs) -> None:
    if not attrs:
        return
    expr_names = {f"#{k}": k for k in attrs}
    expr_values = {f":{k}": v for k, v in attrs.items()}
    update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in attrs)
    _table(table_name).update_item(
        Key={PK: job_pk(job_id)},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


def conditional_status_flip(table_name: str, job_id: str, from_status: str, to_status: str) -> bool:
    table = _table(table_name)
    try:
        table.update_item(
            Key={PK: job_pk(job_id)},
            UpdateExpression="SET #status = :to",
            ConditionExpression="#status = :from",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":to": to_status, ":from": from_status},
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def mark_failed(table_name: str, job_id: str, error: str) -> None:
    update_job(table_name, job_id, status="FAILED", error=error)
