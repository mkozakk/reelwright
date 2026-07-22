from __future__ import annotations

import time

import boto3

from .dynamo import PK

SEMAPHORE_PK = "SEMAPHORE#render"


def _table(table_name: str):
    return boto3.resource("dynamodb").Table(table_name)


def _ensure_item(table) -> None:
    try:
        table.put_item(
            Item={PK: SEMAPHORE_PK, "held": 0, "holders": {}},
            ConditionExpression="attribute_not_exists(#pk)",
            ExpressionAttributeNames={"#pk": PK},
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        pass


def acquire_slot(table_name: str, job_id: str, cap: int) -> bool:
    table = _table(table_name)
    _ensure_item(table)
    try:
        table.update_item(
            Key={PK: SEMAPHORE_PK},
            UpdateExpression="SET held = held + :one, holders.#job = :now",
            ConditionExpression="held < :cap",
            ExpressionAttributeNames={"#job": job_id},
            ExpressionAttributeValues={":one": 1, ":cap": cap, ":now": int(time.time())},
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def release_slot(table_name: str, job_id: str) -> None:
    table = _table(table_name)
    try:
        table.update_item(
            Key={PK: SEMAPHORE_PK},
            UpdateExpression="SET held = held - :one REMOVE holders.#job",
            ConditionExpression="held > :zero",
            ExpressionAttributeNames={"#job": job_id},
            ExpressionAttributeValues={":one": 1, ":zero": 0},
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        pass
