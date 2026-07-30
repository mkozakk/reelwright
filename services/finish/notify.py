from __future__ import annotations

import boto3

SUBJECT = "Your montage is ready"


def send_completion_email(from_email: str, to_email: str, signed_url: str) -> None:
    boto3.client("ses").send_email(
        Source=from_email,
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": SUBJECT},
            "Body": {"Text": {"Data": f"Your montage is ready: {signed_url}"}},
        },
    )
