from __future__ import annotations

from decimal import Decimal

import boto3

from .models import JobRecord, SourceRef

PK = "pk"


def _table(table_name: str):
    return boto3.resource("dynamodb").Table(table_name)


def job_pk(job_id: str) -> str:
    return f"JOB#{job_id}"


def to_decimal(value):
    """DynamoDB's Number type has no float representation -- recursively
    convert floats (e.g. inside an edit_plan) to Decimal before a write."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_decimal(v) for v in value]
    return value


def to_native(value):
    """Inverse of to_decimal: every Number read back from DynamoDB arrives
    as Decimal regardless of whether it was written as an int or a float."""
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if as_int == value else float(value)
    if isinstance(value, dict):
        return {k: to_native(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_native(v) for v in value]
    return value


def get_job(table_name: str, job_id: str) -> JobRecord:
    raw_item = _table(table_name).get_item(Key={PK: job_pk(job_id)}).get("Item")
    if raw_item is None:
        raise KeyError(f"no job record for '{job_id}'")
    item = to_native(raw_item)
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
        planning=item.get("planning"),
        analysis_keys=dict(item.get("analysis_keys", {})),
        target_profile=item.get("target_profile"),
        output_key=item.get("output_key"),
        thumbnail_key=item.get("thumbnail_key"),
        error=item.get("error"),
        notify_email=item.get("notify_email"),
        ttl=item.get("ttl"),
    )


def put_new_job(table_name: str, item: dict) -> None:
    _table(table_name).put_item(
        Item=to_decimal(item),
        ConditionExpression="attribute_not_exists(pk)",
    )


def update_job(table_name: str, job_id: str, **attrs) -> None:
    if not attrs:
        return
    expr_names = {f"#{k}": k for k in attrs}
    expr_values = {f":{k}": to_decimal(v) for k, v in attrs.items()}
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


def ip_cap_pk(ip: str, day: str) -> str:
    return f"IPCAP#{ip}#{day}"


def claim_ip_slot(table_name: str, ip: str, day: str, cap: int, ttl: int) -> bool:
    """Atomically bump a per-IP-per-day counter, refusing once it reaches cap.
    'count' is a DynamoDB reserved word, hence the #n alias. Returns False when
    the daily cap is already spent -- the caller turns that into HTTP 429."""
    table = _table(table_name)
    try:
        table.update_item(
            Key={PK: ip_cap_pk(ip, day)},
            UpdateExpression="SET #ttl = if_not_exists(#ttl, :ttl) ADD #n :one",
            ConditionExpression="attribute_not_exists(#n) OR #n < :cap",
            ExpressionAttributeNames={"#n": "count", "#ttl": "ttl"},
            ExpressionAttributeValues={":one": 1, ":cap": cap, ":ttl": ttl},
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def mark_failed(table_name: str, job_id: str, error: str) -> None:
    update_job(table_name, job_id, status="FAILED", error=error)


def set_analysis_key(table_name: str, job_id: str, category: str, value: dict) -> None:
    # Nested SET on one leaf, not update_job's blind top-level SET -- safe for
    # concurrent Analyze branches writing different categories on the same item.
    table = _table(table_name)
    key = {PK: job_pk(job_id)}
    table.update_item(
        Key=key,
        UpdateExpression="SET analysis_keys = if_not_exists(analysis_keys, :empty)",
        ExpressionAttributeValues={":empty": {}},
    )
    table.update_item(
        Key=key,
        UpdateExpression="SET analysis_keys.#category = :val",
        ExpressionAttributeNames={"#category": category},
        ExpressionAttributeValues={":val": to_decimal(value)},
    )
