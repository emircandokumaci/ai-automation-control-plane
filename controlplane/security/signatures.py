from __future__ import annotations
import hashlib
import hmac
import time


def sign_payload(secret: str, timestamp: str, event_id: str, raw_body: bytes) -> str:
    msg = timestamp.encode() + b"." + event_id.encode() + b"." + raw_body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, event_id: str, raw_body: bytes, signature: str, max_age_seconds: int = 300) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > max_age_seconds:
        return False
    expected = sign_payload(secret, timestamp, event_id, raw_body)
    return hmac.compare_digest(expected, signature)
