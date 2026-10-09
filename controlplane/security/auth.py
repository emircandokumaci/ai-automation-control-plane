from __future__ import annotations
import hashlib
import hmac
from fastapi import HTTPException, Request
from sqlalchemy import select
from ..config import OWNER_USERNAME, OWNER_PASSWORD
from ..db import SessionLocal
from ..models import ServiceToken, utcnow


def verify_owner(username: str, password: str) -> bool:
    if not OWNER_PASSWORD:
        return False
    return hmac.compare_digest(username, OWNER_USERNAME) and hmac.compare_digest(password, OWNER_PASSWORD)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_actor(request: Request, required_scope: str | None = None) -> tuple[str, str]:
    if request.session.get("user"):
        return ("user", str(request.session["user"]))
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        digest = token_hash(token)
        with SessionLocal() as db:
            row = db.scalar(select(ServiceToken).where(ServiceToken.token_hash == digest, ServiceToken.enabled.is_(True)))
            if row:
                if required_scope and required_scope not in (row.scopes or []) and "admin:*" not in (row.scopes or []):
                    raise HTTPException(status_code=403, detail="insufficient token scope")
                row.last_used_at = utcnow()
                db.commit()
                return ("service", row.name)
    raise HTTPException(status_code=401, detail="authentication required")


def require_session(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return str(user)
