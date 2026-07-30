from __future__ import annotations

import contextlib
import traceback

from aws_xray_sdk.core import patch_all, xray_recorder

# LOG_ERROR (not the default RUNTIME_ERROR) so a segment() call made outside
# an active Lambda trace context (e.g. a unit test) logs instead of raising --
# AWS_XRAY_SDK_ENABLED=false (tests/conftest.py) then makes patch_all's boto3
# instrumentation and every segment()/subsegment call a no-op on top of that.
xray_recorder.configure(context_missing="LOG_ERROR")
patch_all()


@contextlib.contextmanager
def segment(name: str, job_id: str | None):
    sub = xray_recorder.begin_subsegment(name)
    if sub is not None and job_id is not None:
        sub.put_annotation("job_id", job_id)
    try:
        yield
    except Exception as exc:
        if sub is not None:
            sub.add_exception(exc, traceback.extract_stack())
        raise
    finally:
        xray_recorder.end_subsegment()
