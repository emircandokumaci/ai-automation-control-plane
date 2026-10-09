from __future__ import annotations
import json
import time
from pathlib import Path
import httpx
from .base import Adapter, AdapterResult, ExecutionContext
from ..config import YOUTUBE_HEALTH_FILE, BINANCE_HEALTH_URL


def _echo(payload: dict) -> dict:
    return {"echo": payload.get("input")}


def _sleep(payload: dict) -> dict:
    seconds = max(0.0, min(float(payload.get("seconds", 1)), 10.0))
    time.sleep(seconds)
    return {"slept": seconds}


def _youtube_health(payload: dict) -> dict:
    p = Path(YOUTUBE_HEALTH_FILE)
    if not p.exists():
        return {"ok": False, "status": "UNKNOWN", "reason": "health file missing"}
    data = json.loads(p.read_text())
    return {
        "ok": bool(data.get("ok")),
        "status": "HEALTHY" if data.get("ok") else "DEGRADED",
        "safe_mode": data.get("safe_mode"),
        "publishing_enabled": data.get("publishing_enabled"),
        "warnings": data.get("warnings", []),
        "errors": data.get("errors", []),
        "disk_free_gb": data.get("disk_free_gb"),
        "ready_manifests": data.get("ready_manifests"),
        "claims": data.get("claims"),
        "receipts": data.get("receipts"),
    }


def _binance_health(payload: dict) -> dict:
    with httpx.Client(timeout=5) as client:
        r = client.get(BINANCE_HEALTH_URL)
    r.raise_for_status()
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text[:500]}
    return {"ok": True, "status": "HEALTHY", "engine": data}


REGISTRY = {
    "system.echo": _echo,
    "system.sleep": _sleep,
    "youtube.health": _youtube_health,
    "binance.health": _binance_health,
}


class PythonAdapter(Adapter):
    name = "python"

    def execute(self, ctx: ExecutionContext, payload: dict) -> AdapterResult:
        callable_name = str(payload.get("callable") or "")
        fn = REGISTRY.get(callable_name)
        if fn is None:
            return AdapterResult(status="FAILED", error_class="VALIDATION", error_code="UNKNOWN_CALLABLE", error_message=f"callable not registered: {callable_name}")
        try:
            out = fn(payload)
            ok = bool(out.get("ok", True)) if isinstance(out, dict) else True
            if callable_name.endswith(".health") and not ok:
                return AdapterResult(status="FAILED", receipt=out, error_class="BUSINESS_RULE", error_code="HEALTH_DEGRADED", error_message=json.dumps(out)[:500])
            return AdapterResult(status="SUCCESS", receipt={"callable": callable_name, "output": out})
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            err = "UPSTREAM_5XX" if code >= 500 else "VALIDATION"
            return AdapterResult(status="FAILED", error_class=err, error_code=f"HTTP_{code}", error_message=str(exc))
        except httpx.TimeoutException as exc:
            return AdapterResult(status="FAILED", error_class="TRANSIENT_NETWORK", error_message=str(exc))
        except Exception as exc:
            return AdapterResult(status="FAILED", error_class="BUSINESS_RULE", error_code=type(exc).__name__, error_message=str(exc))
