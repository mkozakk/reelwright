from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone

from services.common import s3keys, session_caps

MAX_FILE_BYTES = 500 * 1024 * 1024  # mirrors renderer.probe; re-checked in Probe
ALLOWED_VIDEO_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
# mp3 accepted alongside aac/opus containers (ADR-4, docs/phases/phase-8.md) --
# a real UX cost avoided at the cost of a wider untrusted-media decoder
# surface in Probe (still VPC-isolated/no-egress).
ALLOWED_AUDIO_CONTENT_TYPES = {"audio/mp4", "audio/aac", "audio/ogg", "audio/mpeg"}
ALLOWED_ASPECTS = {"16:9", "9:16", "1:1"}
MAX_DURATION_SECONDS = 300  # output.max_duration pref cap -- unrelated to
# session_caps.MAX_SESSION_VIDEO_SECONDS (that one bounds total *uploaded*
# video across a session; this one bounds the requested *output* length)
MAX_VIBE_LEN = 400  # room for real editorial instructions, not just a mood word

MULTIPART_THRESHOLD = 100 * 1024 * 1024  # above this a single PUT is worth splitting
PART_SIZE = 16 * 1024 * 1024  # well over S3's 5 MB per-part floor (last part may be smaller)
MAX_PARTS = 10000  # S3's hard ceiling on parts per upload

UPLOAD_URL_TTL = 3600  # 60 min -- 500 MB on a slow uplink can exceed 15 (docs.DESIGN.md 9)
PLAYBACK_URL_TTL = 900  # 15 min for every non-upload signed URL
JOB_TTL_SECONDS = 30 * 24 * 3600  # docs.DESIGN.md 6: item ttl 30 days
IP_DAILY_CAP = 20  # soft denial-of-wallet rail, kept as defense-in-depth (docs.DESIGN.md 10)
USER_DAILY_CAP = 3  # primary quota now that jobs are authenticated (docs/phases/phase-7.md)
# a session claims exactly one quota slot regardless of file count -- claimed
# once per POST /jobs, same as a single-file job


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def new_job_id() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_body(raw: str | None) -> dict:
    if not raw:
        raise ApiError(400, "request body is required")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "request body is not valid JSON")
    if not isinstance(body, dict):
        raise ApiError(400, "request body must be a JSON object")
    return body


def _validate_prefs(raw: object) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ApiError(400, "'prefs' must be an object")
    unknown = set(raw) - {"vibe", "max_duration", "aspect", "subtitles_enabled"}
    if unknown:
        raise ApiError(400, f"unknown pref(s): {', '.join(sorted(unknown))}")

    prefs: dict = {}
    if "vibe" in raw:
        vibe = raw["vibe"]
        if not isinstance(vibe, str) or not vibe.strip():
            raise ApiError(400, "'vibe' must be a non-empty string")
        if len(vibe) > MAX_VIBE_LEN:
            raise ApiError(400, f"'vibe' exceeds {MAX_VIBE_LEN} characters")
        prefs["vibe"] = vibe.strip()
    if "max_duration" in raw:
        md = raw["max_duration"]
        if not isinstance(md, (int, float)) or isinstance(md, bool) or md <= 0:
            raise ApiError(400, "'max_duration' must be a positive number")
        if md > MAX_DURATION_SECONDS:
            raise ApiError(400, f"'max_duration' exceeds {MAX_DURATION_SECONDS}s")
        prefs["max_duration"] = md
    if "aspect" in raw:
        if raw["aspect"] not in ALLOWED_ASPECTS:
            raise ApiError(400, f"'aspect' must be one of {sorted(ALLOWED_ASPECTS)}")
        prefs["aspect"] = raw["aspect"]
    if "subtitles_enabled" in raw:
        if not isinstance(raw["subtitles_enabled"], bool):
            raise ApiError(400, "'subtitles_enabled' must be a boolean")
        prefs["subtitles_enabled"] = raw["subtitles_enabled"]
    return prefs


def _validate_file(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ApiError(400, "each file must be an object")

    content_type = raw.get("content_type")
    if content_type in ALLOWED_VIDEO_CONTENT_TYPES:
        kind = "video"
    elif content_type in ALLOWED_AUDIO_CONTENT_TYPES:
        kind = "audio"
    else:
        allowed = sorted(ALLOWED_VIDEO_CONTENT_TYPES | ALLOWED_AUDIO_CONTENT_TYPES)
        raise ApiError(400, f"'content_type' must be one of {allowed}")

    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ApiError(400, "'size' must be a positive integer (bytes)")
    if size > MAX_FILE_BYTES:
        raise ApiError(400, f"'size' exceeds the {MAX_FILE_BYTES}-byte limit")

    return {"content_type": content_type, "kind": kind, "size": size}


def validate_create_request(body: dict) -> dict:
    """Normalise POST /jobs input into {files, prefs}. Caps are enforced here
    at the boundary and re-checked in Probe/SessionValidate (docs.DESIGN.md 5)."""
    raw_files = body.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ApiError(400, "'files' must be a non-empty list")
    if len(raw_files) > session_caps.MAX_FILES:
        raise ApiError(400, f"'files' exceeds the {session_caps.MAX_FILES}-file limit")

    files = [_validate_file(f) for f in raw_files]
    if not any(f["kind"] == "video" for f in files):
        raise ApiError(400, "at least one file must be a video (a session with only audio has nothing to cut)")

    return {
        "files": files,
        "prefs": _validate_prefs(body.get("prefs")),
    }


def wants_multipart(size: int) -> bool:
    return size > MULTIPART_THRESHOLD


def part_count(size: int) -> int:
    return math.ceil(size / PART_SIZE)


def validate_complete_request(body: dict) -> dict:
    """Normalise POST /jobs/{id}/complete into the shape S3's
    CompleteMultipartUpload wants: {src_id, upload_id, parts:[{PartNumber, ETag}]}."""
    src_id = body.get("src_id")
    if not isinstance(src_id, str) or not src_id:
        raise ApiError(400, "'src_id' is required")

    upload_id = body.get("upload_id")
    if not isinstance(upload_id, str) or not upload_id:
        raise ApiError(400, "'upload_id' is required")

    raw_parts = body.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ApiError(400, "'parts' must be a non-empty list")
    if len(raw_parts) > MAX_PARTS:
        raise ApiError(400, f"'parts' exceeds the {MAX_PARTS}-part limit")

    parts = []
    for part in raw_parts:
        if not isinstance(part, dict):
            raise ApiError(400, "each part must be an object")
        number = part.get("part_number")
        etag = part.get("etag")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ApiError(400, "'part_number' must be a positive integer")
        if not isinstance(etag, str) or not etag:
            raise ApiError(400, "'etag' is required for each part")
        parts.append({"PartNumber": number, "ETag": etag})

    parts.sort(key=lambda p: p["PartNumber"])
    return {"src_id": src_id, "upload_id": upload_id, "parts": parts}


def src_ids(n: int) -> list[str]:
    return [f"src{i}" for i in range(1, n + 1)]


def build_job_item(job_id: str, files: list[dict], request: dict, created: datetime, user_id: str) -> dict:
    """The DynamoDB item for a freshly created job. user_id/created_at are
    what GSI1 projects on for the per-user job list (docs/phases/phase-7.md).
    src1..srcN by declaration order -- no migration concern (job records TTL
    at 30 days, raw objects at 48h, one shared dev environment)."""
    return {
        "pk": f"JOB#{job_id}",
        "user_id": user_id,
        "status": "UPLOADING",
        "created_at": created.isoformat(),
        "sources": {
            src_id: {
                "key": s3keys.raw_key(job_id, src_id),
                "kind": f["kind"],
                "size": f["size"],
                "uploaded": False,
            }
            for src_id, f in zip(src_ids(len(files)), files)
        },
        "prefs": request["prefs"],
        "ttl": int(created.timestamp()) + JOB_TTL_SECONDS,
    }


def client_ip(event: dict) -> str:
    return event.get("requestContext", {}).get("http", {}).get("sourceIp", "unknown")


def claims(event: dict) -> dict:
    """Verified JWT claims the API Gateway authorizer attaches to the event --
    no token verification happens in this Lambda, API Gateway already did it."""
    return event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})


def user_id_from_claims(event: dict) -> str:
    sub = claims(event).get("sub")
    if not sub:
        raise ApiError(401, "missing authenticated user")
    return sub


def cap_day(created: datetime) -> str:
    return created.strftime("%Y-%m-%d")


def cap_ttl(created: datetime) -> int:
    return int(created.timestamp()) + 2 * 24 * 3600


def hash_prefs(prefs: dict) -> str:
    canonical = json.dumps(prefs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def start_execution_name(job_id: str, etags: dict[str, str], prefs_hash: str) -> str:
    # idempotent -- a double /start of the same uploaded content doesn't
    # start a second execution. etags come from /start's own HeadObject
    # calls (not cached from job creation), so a re-upload before /start
    # gets a fresh ETag baked in here, same rationale as trigger's etag use.
    etag_payload = ",".join(f"{sid}:{etags[sid]}" for sid in sorted(etags))
    payload = f"{job_id}/{etag_payload}/{prefs_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()


def rerender_execution_name(job_id: str, edit_plan_json: str) -> str:
    # idempotent -- a double-submit of the identical edited plan doesn't
    # start a second execution, same approach as services/trigger/logic.py
    payload = f"{job_id}/{edit_plan_json}"
    return hashlib.sha256(payload.encode()).hexdigest()


def status_response(job) -> dict:
    """Lean GET /jobs/{id} body. The edit plan rides along so the frontend can
    show *why* each moment was chosen (docs.DESIGN.md 6); output URLs are signed
    by the handler and merged in only once the job is DONE."""
    body: dict = {
        "job_id": job.job_id,
        "status": job.status,
        "created_at": job.created_at,
        "prefs": job.prefs,
    }
    if job.edit_plan is not None:
        body["edit_plan"] = job.edit_plan
    if job.error is not None:
        body["error"] = job.error
    return body
