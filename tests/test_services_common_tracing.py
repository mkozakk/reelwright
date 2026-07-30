import pytest

from services.common.tracing import segment


def test_segment_runs_body_and_does_not_raise():
    ran = False
    with segment("test.tracing.a", "job1"):
        ran = True
    assert ran


def test_segment_reraises_on_exception():
    with pytest.raises(ValueError):
        with segment("test.tracing.b", "job1"):
            raise ValueError("boom")


def test_segment_accepts_no_job_id():
    with segment("test.tracing.c", None):
        pass
