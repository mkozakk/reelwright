import json

import pytest

from services.common.logging import get_logger, log_job


def _parsed_lines(capsys):
    out = capsys.readouterr().out.strip()
    return [json.loads(line) for line in out.splitlines() if line]


def test_get_logger_emits_json_with_job_id(capsys):
    log = get_logger("test.logger.a", job_id="job1")
    log.info("hello")

    [payload] = _parsed_lines(capsys)
    assert payload["job_id"] == "job1"
    assert payload["message"] == "hello"
    assert payload["service"] == "test.logger.a"


def test_get_logger_with_no_job_id_serializes_null(capsys):
    log = get_logger("test.logger.b", job_id=None)
    log.info("no job yet")

    [payload] = _parsed_lines(capsys)
    assert payload["job_id"] is None


def test_log_job_logs_start_and_done_on_success(capsys):
    with log_job("test.logger.c", "job1"):
        pass

    messages = [line["message"] for line in _parsed_lines(capsys)]
    assert messages == ["start", "done"]


def test_log_job_logs_and_reraises_on_exception(capsys):
    with pytest.raises(ValueError):
        with log_job("test.logger.d", "job1"):
            raise ValueError("boom")

    messages = [line["message"] for line in _parsed_lines(capsys)]
    assert messages == ["start", "failed"]
