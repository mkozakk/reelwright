import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

OUT_DIR = REPO_ROOT / "out"
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"

JOBS_TABLE = "jobs-test"
RAW_BUCKET = "raw-test"
WORK_BUCKET = "work-test"
OUTPUT_BUCKET = "output-test"


@pytest.fixture(scope="session", autouse=True)
def _ensure_out_dir():
    OUT_DIR.mkdir(exist_ok=True)


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def aws_stack(aws_credentials):
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=JOBS_TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.resource("s3", region_name="us-east-1")
        for name in (RAW_BUCKET, WORK_BUCKET, OUTPUT_BUCKET):
            s3.create_bucket(Bucket=name)
        yield {
            "jobs_table": JOBS_TABLE,
            "raw_bucket": RAW_BUCKET,
            "work_bucket": WORK_BUCKET,
            "output_bucket": OUTPUT_BUCKET,
        }
