from __future__ import annotations
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from sqlalchemy import select
from controlplane.config import WORKER_ID, WORKER_CAPABILITIES, WORKER_CONCURRENCY, LEASE_SECONDS, HEARTBEAT_SECONDS, POLL_SECONDS
from controlplane.db import SessionLocal
from controlplane.models import Workflow
from controlplane.adapters import get_adapter
from controlplane.adapters.base import ExecutionContext
from controlplane.services.jobs import register_worker, heartbeat_worker, claim_job, extend_lease, complete_job, fail_job, defer_job, StaleLeaseError

INSTANCE_ID = str(uuid4())
HOSTNAME = socket.gethostname()


def execute_claim(job, attempt):
    with SessionLocal() as db:
        wf = db.get(Workflow, job.workflow_id)
        if not wf:
            return
        adapter_type = wf.adapter_type
        timeout = wf.timeout_seconds
        retry_policy = wf.retry_policy or {}
    ctx = ExecutionContext(job_id=job.id, attempt_id=attempt.id, project_id=job.project_id, workflow_id=job.workflow_id, idempotency_key=job.idempotency_key, timeout=timeout, lease_token=job.lease_token, trace_id=str(uuid4()))
    try:
        adapter = get_adapter(adapter_type)
        result = adapter.execute(ctx, job.payload or {})
        with SessionLocal() as db:
            if result.status == "SUCCESS":
                complete_job(db, job.id, WORKER_ID, job.lease_token, receipt_payload=result.receipt or {}, external_id=result.external_handle, adapter_type=adapter_type)
            elif result.status == "DEFERRED":
                defer_job(db, job.id, WORKER_ID, job.lease_token, result.external_handle, result.receipt)
            elif result.status == "UNKNOWN_EXTERNAL_STATE":
                fail_job(db, job.id, WORKER_ID, job.lease_token, error_class=result.error_class or "UNKNOWN_EXTERNAL_STATE", error_code=result.error_code, error_message=result.error_message, retry_policy=retry_policy, retry_after=result.retry_after)
            else:
                fail_job(db, job.id, WORKER_ID, job.lease_token, error_class=result.error_class or "BUSINESS_RULE", error_code=result.error_code, error_message=result.error_message, retry_policy=retry_policy, retry_after=result.retry_after)
            db.commit()
    except StaleLeaseError:
        return
    except Exception as exc:
        try:
            with SessionLocal() as db:
                fail_job(db, job.id, WORKER_ID, job.lease_token, error_class="BUSINESS_RULE", error_code=type(exc).__name__, error_message=str(exc), retry_policy=retry_policy)
                db.commit()
        except StaleLeaseError:
            pass


def main():
    with SessionLocal() as db:
        register_worker(db, WORKER_ID, HOSTNAME, INSTANCE_ID, WORKER_CAPABILITIES, WORKER_CONCURRENCY)
        db.commit()
    executor = ThreadPoolExecutor(max_workers=WORKER_CONCURRENCY, thread_name_prefix="cp-job")
    active = {}
    last_hb = 0.0
    while True:
        now = time.monotonic()
        for job_id, item in list(active.items()):
            future, lease_token = item
            if future.done():
                active.pop(job_id, None)
        if now - last_hb >= HEARTBEAT_SECONDS:
            with SessionLocal() as db:
                heartbeat_worker(db, WORKER_ID)
                for job_id, (_, lease_token) in active.items():
                    extend_lease(db, job_id, WORKER_ID, lease_token, LEASE_SECONDS)
                db.commit()
            last_hb = now
        while len(active) < WORKER_CONCURRENCY:
            with SessionLocal() as db:
                claimed = claim_job(db, WORKER_ID, WORKER_CAPABILITIES, LEASE_SECONDS)
                if not claimed:
                    db.rollback()
                    break
                job, attempt = claimed
                db.commit()
            future = executor.submit(execute_claim, job, attempt)
            active[job.id] = (future, job.lease_token)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
