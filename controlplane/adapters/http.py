from __future__ import annotations
from urllib.parse import urlparse
import httpx
from .base import Adapter, AdapterResult, ExecutionContext
from ..config import HTTP_ALLOWED_HOSTS
from ..domain.retry import classify_http


class HTTPAdapter(Adapter):
    name = "http"

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"}:
            raise ValueError("unsupported URL scheme")
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError("missing host")
        if not HTTP_ALLOWED_HOSTS:
            raise ValueError("HTTP adapter allowlist is empty")
        if host not in HTTP_ALLOWED_HOSTS:
            raise ValueError(f"host not allowed: {host}")
        if host in {"169.254.169.254", "metadata.google.internal"}:
            raise ValueError("metadata endpoints are forbidden")

    def execute(self, ctx: ExecutionContext, payload: dict) -> AdapterResult:
        method = str(payload.get("method", "GET")).upper()
        url = str(payload.get("url", ""))
        side_effecting = bool(payload.get("side_effecting", method not in {"GET", "HEAD", "OPTIONS"}))
        headers = dict(payload.get("headers") or {})
        headers.setdefault("User-Agent", "AI-Automation-Control-Plane/0.1")
        try:
            self._validate_url(url)
            with httpx.Client(timeout=min(ctx.timeout, 120), follow_redirects=False) as client:
                r = client.request(method, url, json=payload.get("json"), headers=headers)
            if 200 <= r.status_code < 300:
                data = None
                ctype = r.headers.get("content-type", "")
                if "application/json" in ctype:
                    try:
                        data = r.json()
                    except Exception:
                        data = None
                return AdapterResult(status="SUCCESS", receipt={"status_code": r.status_code, "response": data})
            err = classify_http(status_code=r.status_code)
            return AdapterResult(status="FAILED", error_class=err, error_code=f"HTTP_{r.status_code}", error_message=r.text[:500], retry_after=_retry_after(r))
        except httpx.TimeoutException as exc:
            if side_effecting:
                return AdapterResult(status="UNKNOWN_EXTERNAL_STATE", error_class="TIMEOUT_UNKNOWN_SIDE_EFFECT", error_message=str(exc))
            return AdapterResult(status="FAILED", error_class="TRANSIENT_NETWORK", error_message=str(exc))
        except httpx.HTTPError as exc:
            return AdapterResult(status="FAILED", error_class="TRANSIENT_NETWORK", error_message=str(exc))
        except ValueError as exc:
            return AdapterResult(status="FAILED", error_class="VALIDATION", error_message=str(exc))


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
