import json

from services.common import events


class _FakeEventsClient:
    def __init__(self):
        self.calls = []

    def put_events(self, Entries):
        self.calls.append(Entries)


def test_publish_sends_source_detail_type_and_detail_fields(monkeypatch):
    fake = _FakeEventsClient()
    monkeypatch.setattr(events.boto3, "client", lambda service: fake)

    events.publish("job.created", "job1", user_id="user-1", status="UPLOADING")

    [entries] = fake.calls
    [entry] = entries
    assert entry["Source"] == "montage.pipeline"
    assert entry["DetailType"] == "job.created"

    detail = json.loads(entry["Detail"])
    assert detail["job_id"] == "job1"
    assert detail["user_id"] == "user-1"
    assert detail["status"] == "UPLOADING"
    assert "timestamp" in detail
