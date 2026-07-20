import pytest

from services.common import semaphore
from services.semaphore.handler import SlotUnavailable, acquire_handler, release_handler


def test_acquire_handler_raises_when_cap_reached(aws_stack, monkeypatch):
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("RENDER_CONCURRENCY_CAP", "1")

    assert acquire_handler({"job_id": "job1"}) == {"job_id": "job1"}
    with pytest.raises(SlotUnavailable):
        acquire_handler({"job_id": "job2"})


def test_release_handler_frees_a_slot_for_the_next_job(aws_stack, monkeypatch):
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("RENDER_CONCURRENCY_CAP", "1")

    acquire_handler({"job_id": "job1"})
    assert release_handler({"job_id": "job1"}) == {"job_id": "job1"}
    assert semaphore.acquire_slot(aws_stack["jobs_table"], "job2", cap=1) is True
