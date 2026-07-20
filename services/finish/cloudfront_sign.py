from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key


def build_canned_policy(url: str, expires_at: int) -> bytes:
    policy = {
        "Statement": [
            {"Resource": url, "Condition": {"DateLessThan": {"AWS:EpochTime": expires_at}}}
        ]
    }
    return json.dumps(policy, separators=(",", ":")).encode()


def _url_safe_b64(data: bytes) -> str:
    encoded = base64.b64encode(data).decode()
    return encoded.replace("+", "-").replace("=", "_").replace("/", "~")


def sign_url(url: str, key_pair_id: str, private_key_pem: bytes, expires_at: int) -> str:
    policy = build_canned_policy(url, expires_at)
    private_key = load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(policy, padding.PKCS1v15(), hashes.SHA1())

    separator = "&" if "?" in url else "?"
    query = (
        f"Expires={expires_at}"
        f"&Signature={_url_safe_b64(signature)}"
        f"&Key-Pair-Id={key_pair_id}"
    )
    return f"{url}{separator}{query}"
