from __future__ import annotations
import hashlib
import json
from datetime import timedelta
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ..models import Project, Workflow, Job, JobAttempt, Event, AuditLog, Worker, IdempotencyKey, Receipt, utcnow
from ..domain.retry import should_retry, needs_reconciliation, backoff_seconds


def canonical_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def event(db: Session, *, project_id: str, event_type: str, source: str, job_id: str | None = None, attempt_id: str | None = None, payload: dict | None = None) -> Event:
    row = Event(project_id=project_id, job_id=job_id, attempt_id=attempt_id, event_type=event_type, source=source, payload=payload or {})
    db.add(row)
    return row


def audit(db: Session, *, actor_type: str, actor_id: str, action: str, object_type: str, object_id: str | None = None, metadata: dict | None = None, ip_address: str | None = None, user_agent: str | None = None) -> AuditLog:
    row = AuditLog(actor_type=actor_type, actor_id=actor_id, action=action, object_type=object_type, object_id=object_id, metadata_json=metadata or {}, ip_address=ip_address, user_agent=user_agent)
    db.add(row)
    return row


def create_job(db: Session, workflow_id: str, payload: dict | None = None, *, idempotency_key: str | None = None, priority: int = 0, scheduled_for=None, schedule_id: str | None = None, scheduled_instance_at=None, source: str = "api") -> tuple[Job, bool]:
    workflow = db.get(Workflow, workflow_id)
    if workflow is None or not workflow.enabled:
        raise ValueError("workflow not found or disabled")
    payload = payload or {}
    req_hash = canonical_hash(payload)
    if idempotency_key:
        existing = db.scalar(select(IdempotencyKey).where(IdempotencyKey.project_id == workflow.project_id, IdempotencyKey.scope == f"job.create:{workflow_id}", IdempotencyKey.key == idempotency_key))
        if existing:
            if existing.request_hash != req_hash:
                raise ConflictError("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD")
            if existing.job_id:
                job = db.get(Job, existing.job_id)
                if job:
                    return job, False
    max_attempts = int((workflow.retry_policy or {}).get("max_attempts", 3))
    job = Job(project_id=workflow.project_id, workflow_id=workflow.id, status="QUEUED", priority=priority, scheduled_for=scheduled_for or utcnow(), required_capability=workflow.required_capability, payload=payload, idempotency_key=idempotency_key, dedupe_scope=f"job.create:{workflow_id}" if idempotency_key else None, max_attempts=max_attempts, schedule_id=schedule_id, scheduled_instance_at=scheduled_instance_at)
    db.add(job)
    db.flush()
    if idempotency_key:
        idem = IdempotencyKey(project_id=workflow.project_id, scope=f"job.create:{workflow_id}", key=idempotency_key, request_hash=req_hash, job_id=job.id, status="CLAIMED")
        db.add(idem)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            existing = db.scalar(select(IdempotencyKey).where(IdempotencyKey.project_id == workflow.project_id, IdempotencyKey.scope == f"job.create:{workflow_id}", IdempotencyKey.key == idempotency_key))
            if existing and existing.request_hash == req_hash and existing.job_id:
                job = db.get(Job, existing.job_id)
                if job:
                    return job, False
            raise exc
    event(db, project_id=workflow.project_id, job_id=job.id, event_type="job.created", source=source, payload={"priority": priority, "scheduled_for": job.scheduled_for.isoformat()})
    return job, True


class ConflictError(Exception):
    pass


def register_worker(db: Session, worker_id: str, hostname: str, instance_id: str, capabilities: list[str], max_concurrency: int) -> Worker:
    row = db.get(Worker, worker_id)
    if row is None:
        row = Worker(id=worker_id, hostname=hostname, instance_id=instance_id, capabilities=capabilities, max_concurrency=max_concurrency, started_at=utcnow(), last_heartbeat_at=utcnow(), status="HEALTHY")
        db.add(row)
    else:
        row.hostname = hostname
        row.instance_id = instance_id
        row.capabilities = capabilities
        row.max_concurrency = max_concurrency
        row.last_heartbeat_at = utcnow()
        row.status = "HEALTHY"
    return row


def heartbeat_worker(db: Session, worker_id: str) -> None:
    row = db.get(Worker, worker_id)
    if row:
        row.last_heartbeat_at = utcnow()
        row.status = "HEALTHY"


def claim_job(db: Session, worker_id: str, capabilities: list[str], lease_seconds: int) -> tuple[Job, JobAttempt] | None:
    now = utcnow()
    candidates = list(db.scalars(select(Job).where(Job.status.in_(["QUEUED", "RETRY_WAIT"]), Job.scheduled_for <= now, Job.required_capability.in_(capabilities)).order_by(Job.priority.desc(), Job.scheduled_for.asc(), Job.created_at.asc()).with_for_update(skip_locked=True).limit(12)))
    for job in candidates:
        workflow = db.get(Workflow, job.workflow_id)
        if workflow is None or not workflow.enabled:
            continue
        running = db.scalar(select(func.count()).select_from(Job).where(Job.workflow_id == workflow.id, Job.status.in_(["RUNNING", "WAITING_EXTERNAL", "RECONCILING", "CANCEL_REQUESTED"]))) or 0
        if running >= max(1, workflow.max_concurrency):
            continue
        job.status = "RUNNING"
        job.worker_id = worker_id
        job.lease_token += 1
        job.leased_until = now + timedelta(seconds=lease_seconds)
        job.attempt_count += 1
        if job.started_at is None:
            job.started_at = now
        attempt = JobAttempt(job_id=job.id, attempt_number=job.attempt_count, worker_id=worker_id, lease_token=job.lease_token, status="RUNNING", started_at=now, heartbeat_at=now)
        db.add(attempt)
        db.flush()
        event(db, project_id=job.project_id, job_id=job.id, attempt_id=attempt.id, event_type="job.claimed", source="worker", payload={"worker_id": worker_id, "lease_token": job.lease_token})
        return job, attempt
    return None


def extend_lease(db: Session, job_id: str, worker_id: str, lease_token: int, lease_seconds: int) -> bool:
    job = db.get(Job, job_id)
    if not job or job.worker_id != worker_id or job.lease_token != lease_token or job.status not in {"RUNNING", "CANCEL_REQUESTED"}:
        return False
    job.leased_until = utcnow() + timedelta(seconds=lease_seconds)
    attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt_number == job.attempt_count))
    if attempt:
        attempt.heartbeat_at = utcnow()
    return True


def _fence(db: Session, job_id: str, worker_id: str, lease_token: int) -> tuple[Job, JobAttempt]:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.worker_id != worker_id or job.lease_token != lease_token:
        raise StaleLeaseError(job_id)
    attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt_number == job.attempt_count))
    if not attempt:
        raise StaleLeaseError(job_id)
    return job, attempt


class StaleLeaseError(Exception):
    pass


def complete_job(db: Session, job_id: str, worker_id: str, lease_token: int, *, receipt_payload: dict | None = None, external_id: str | None = None, adapter_type: str = "unknown") -> Job:
    job, attempt = _fence(db, job_id, worker_id, lease_token)
    now = utcnow()
    job.status = "SUCCEEDED"
    job.finished_at = now
    job.leased_until = None
    attempt.status = "SUCCEEDED"
    attempt.finished_at = now
    if receipt_payload is not None:
        receipt = Receipt(project_id=job.project_id, job_id=job.id, attempt_id=attempt.id, adapter_type=adapter_type, operation=f"{adapter_type}.execute", idempotency_key=job.idempotency_key, external_id=external_id, receipt_payload=receipt_payload, verified_at=now)
        db.add(receipt)
    if job.idempotency_key:
        idem = db.scalar(select(IdempotencyKey).where(IdempotencyKey.project_id == job.project_id, IdempotencyKey.scope == job.dedupe_scope, IdempotencyKey.key == job.idempotency_key))
        if idem:
            idem.status = "SUCCEEDED"
    event(db, project_id=job.project_id, job_id=job.id, attempt_id=attempt.id, event_type="job.completed", source="worker", payload={"status": "SUCCEEDED"})
    return job


def fail_job(db: Session, job_id: str, worker_id: str, lease_token: int, *, error_class: str, error_code: str | None, error_message: str | None, retry_policy: dict | None = None, retry_after: float | None = None) -> Job:
    job, attempt = _fence(db, job_id, worker_id, lease_token)
    now = utcnow()
    attempt.status = "FAILED"
    attempt.finished_at = now
    attempt.error_class = error_class
    attempt.error_code = error_code
    attempt.error_message = (error_message or "")[:4000]
    job.last_error_class = error_class
    job.last_error_code = error_code
    job.last_error_message = (error_message or "")[:4000]
    job.leased_until = None
    job.worker_id = None
    if needs_reconciliation(error_class):
        job.status = "RECONCILING"
        event(db, project_id=job.project_id, job_id=job.id, attempt_id=attempt.id, event_type="job.reconciliation_required", source="worker", payload={"error_class": error_class})
        return job
    if should_retry(error_class) and job.attempt_count < job.max_attempts:
        policy = retry_policy or {}
        delay = backoff_seconds(job.attempt_count, int(policy.get("base_seconds", 5)), int(policy.get("cap_seconds", 900)), retry_after)
        job.status = "RETRY_WAIT"
        job.scheduled_for = now + timedelta(seconds=delay)
        event(db, project_id=job.project_id, job_id=job.id, attempt_id=attempt.id, event_type="attempt.retry_scheduled", source="worker", payload={"delay_seconds": round(delay, 3), "error_class": error_class})
        return job
    job.status = "DEAD" if should_retry(error_class) or error_class == "POISON" else "FAILED"
    job.finished_at = now
    event(db, project_id=job.project_id, job_id=job.id, attempt_id=attempt.id, event_type="job.failed", source="worker", payload={"status": job.status, "error_class": error_class, "error_code": error_code})
    return job


def defer_job(db: Session, job_id: str, worker_id: str, lease_token: int, external_handle: str | None, receipt_payload: dict | None = None) -> Job:
    job, attempt = _fence(db, job_id, worker_id, lease_token)
    job.status = "WAITING_EXTERNAL"
    job.external_handle = external_handle
    job.leased_until = None
    job.worker_id = None
    attempt.status = "DEFERRED"
    attempt.external_handle = external_handle
    attempt.finished_at = utcnow()
    event(db, project_id=job.project_id, job_id=job.id, attempt_id=attempt.id, event_type="job.waiting_external", source="worker", payload={"external_handle": external_handle, "receipt": receipt_payload or {}})
    return job


def request_cancel(db: Session, job: Job) -> Job:
    if job.status in {"QUEUED", "RETRY_WAIT"}:
        job.status = "CANCELLED"
        job.finished_at = utcnow()
    elif job.status in {"RUNNING", "WAITING_EXTERNAL", "RECONCILING"}:
        job.status = "CANCEL_REQUESTED"
        job.cancel_requested_at = utcnow()
    return job


def redrive_job(db: Session, job: Job, *, source: str = "ui") -> Job:
    new_job = Job(project_id=job.project_id, workflow_id=job.workflow_id, status="QUEUED", stage=job.stage, priority=job.priority, scheduled_for=utcnow(), required_capability=job.required_capability, payload=job.payload, attempt_count=0, max_attempts=job.max_attempts, redrive_of_job_id=job.id)
    db.add(new_job)
    db.flush()
    event(db, project_id=job.project_id, job_id=new_job.id, event_type="job.redriven", source=source, payload={"redrive_of_job_id": job.id})
    return new_job


def reap_expired(db: Session) -> int:
    now = utcnow()
    jobs = list(db.scalars(select(Job).where(Job.status == "RUNNING", Job.leased_until.is_not(None), Job.leased_until < now).with_for_update(skip_locked=True).limit(50)))
    count = 0
    for job in jobs:
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt_number == job.attempt_count))
        if attempt and attempt.status == "RUNNING":
            attempt.status = "LOST"
            attempt.finished_at = now
            attempt.error_class = "LOST_WORKER"
            attempt.error_message = "worker lease expired"
        job.last_error_class = "LOST_WORKER"
        job.last_error_message = "worker lease expired"
        job.worker_id = None
        job.leased_until = None
        if job.attempt_count < job.max_attempts:
            job.status = "RETRY_WAIT"
            job.scheduled_for = now + timedelta(seconds=5)
        else:
            job.status = "DEAD"
            job.finished_at = now
        event(db, project_id=job.project_id, job_id=job.id, attempt_id=attempt.id if attempt else None, event_type="worker.lease_expired", source="reaper", payload={"new_status": job.status})
        count += 1
    return count
