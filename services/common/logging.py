from __future__ import annotations

import contextlib
import json
import logging
import sys
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": record.name,
            "message": record.getMessage(),
            "job_id": getattr(record, "job_id", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def get_logger(name: str, job_id: str | None = None) -> logging.LoggerAdapter:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logging.LoggerAdapter(logger, {"job_id": job_id})


@contextlib.contextmanager
def log_job(name: str, job_id: str | None):
    log = get_logger(name, job_id)
    log.info("start")
    try:
        yield log
    except Exception:
        log.exception("failed")
        raise
    log.info("done")
