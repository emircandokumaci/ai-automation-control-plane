from __future__ import annotations
import httpx
from .base import Adapter, AdapterResult, ExecutionContext
from ..config import N8N_BASE_URL


class N8NAdapter(Adapter):
    name = "n8n"

    def execute(self, ctx: ExecutionContext, payload: dict) -> AdapterResult:
        path = str(payload.get("webhook_path") or "").strip()
        if not path.startswith("/"):
            path = "/" + path
        if not path or path == "/":
            return AdapterResult(status="FAILED", error_class="VALIDATION", error_message="webhook_path is required")
        url = N8N_BASE_URL.rstrip("/") + path
        body = dict(payload.get("body") or {})
        body.setdefault("control_plane_job_id", ctx.job_id)
        body.setdefault("idempotency_key", ctx.idempotency_key)
        headers = {"X-Control-Plane-Job": ctx.job_id}
        try:
            with httpx.Client(timeout=min(ctx.timeout, 120), follow_redirects=False) as client:
                r = client.post(url, json=body, headers=headers)
            if 200 <= r.status_code < 300:
                data = None
                try:
                    data = r.json()
                except Exception:
                    data = {"text": r.text[:500]}
                handle = None
                if isinstance(data, dict):
                    handle = str(data.get("executionId") or data.get("execution_id") or "") or None
                if bool(payload.get("async", False)):
                    return AdapterResult(status="DEFERRED", external_handle=handle, receipt={"trigger_status": r.status_code, "response": data})
                return AdapterResult(status="SUCCESS", external_handle=handle, receipt={"trigger_status": r.status_code, "response": data})
            if r.status_code == 429:
                return AdapterResult(status="FAILED", error_class="RATE_LIMITED", error_code="HTTP_429", error_message=r.text[:500])
            if r.status_code >= 500:
                return AdapterResult(status="FAILED", error_class="UPSTREAM_5XX", error_code=f"HTTP_{r.status_code}", error_message=r.text[:500])
            return AdapterResult(status="FAILED", error_class="VALIDATION", error_code=f"HTTP_{r.status_code}", error_message=r.text[:500])
        except httpx.TimeoutException as exc:
            return AdapterResult(status="UNKNOWN_EXTERNAL_STATE", error_class="TIMEOUT_UNKNOWN_SIDE_EFFECT", error_message=str(exc))
        except httpx.HTTPError as exc:
            return AdapterResult(status="FAILED", error_class="TRANSIENT_NETWORK", error_message=str(exc))

    def health(self) -> dict:
        try:
            with httpx.Client(timeout=5) as client:
                r = client.get(N8N_BASE_URL.rstrip("/") + "/healthz")
            return {"ok": r.status_code == 200, "status_code": r.status_code, "adapter": self.name}
        except Exception as exc:
            return {"ok": False, "adapter": self.name, "error": str(exc)}
