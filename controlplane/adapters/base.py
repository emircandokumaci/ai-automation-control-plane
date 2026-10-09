from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    job_id: str
    attempt_id: str
    project_id: str
    workflow_id: str
    idempotency_key: str | None
    timeout: int
    lease_token: int
    trace_id: str


@dataclass
class AdapterResult:
    status: str
    external_handle: str | None = None
    receipt: dict[str, Any] | None = None
    output_refs: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    error_class: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_after: float | None = None


class Adapter:
    name = "base"

    def execute(self, ctx: ExecutionContext, payload: dict) -> AdapterResult:
        raise NotImplementedError

    def reconcile(self, ctx: ExecutionContext, payload: dict, receipt: dict | None = None) -> AdapterResult:
        return AdapterResult(status="UNKNOWN_EXTERNAL_STATE", error_class="UNKNOWN_EXTERNAL_STATE", error_message="No reconciliation strategy")

    def cancel(self, ctx: ExecutionContext, handle: str | None) -> AdapterResult:
        return AdapterResult(status="FAILED", error_class="UNSUPPORTED", error_message="Cancellation is not supported")

    def health(self) -> dict:
        return {"ok": True, "adapter": self.name}
