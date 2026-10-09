from __future__ import annotations
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from croniter import croniter
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from controlplane.config import SCHEDULER_LOCK_KEY
from controlplane.db import SessionLocal, engine
from controlplane.models import Schedule, Workflow, utcnow
from controlplane.services.jobs import create_job, reap_expired


def next_occurrence(cron_expr: str, tz_name: str, after_utc: datetime) -> datetime:
    tz = ZoneInfo(tz_name)
    local_after = after_utc.astimezone(tz)
    nxt_local = croniter(cron_expr, local_after).get_next(datetime)
    if nxt_local.tzinfo is None:
        nxt_local = nxt_local.replace(tzinfo=tz)
    return nxt_local.astimezone(timezone.utc)


def schedule_due_jobs(db):
    now = utcnow()
    due = list(db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run_at <= now).order_by(Schedule.next_run_at.asc()).with_for_update(skip_locked=True).limit(50)))
    created = 0
    for sched in due:
        wf = db.get(Workflow, sched.workflow_id)
        if wf is None or not wf.enabled:
            sched.next_run_at = next_occurrence(sched.cron_expression, sched.timezone, now)
            continue
        instance = sched.next_run_at
        should_run = True
        if instance < now:
            lag = (now - instance).total_seconds()
            if sched.misfire_policy == "SKIP" and lag > 60:
                should_run = False
        if should_run:
            key = f"schedule:{sched.id}:{instance.isoformat()}"
            try:
                create_job(db, wf.id, dict((wf.config or {}).get("default_payload") or {}), idempotency_key=key, schedule_id=sched.id, scheduled_instance_at=instance, source="scheduler")
                created += 1
            except (IntegrityError, Exception) as exc:
                # Duplicate schedule instances are harmless; unexpected failures are retried next loop.
                if not isinstance(exc, IntegrityError):
                    raise
        sched.last_scheduled_at = instance
        sched.next_run_at = next_occurrence(sched.cron_expression, sched.timezone, max(instance, now))
    return created


def main():
    # Dedicated connection holds advisory lock for leader lifetime.
    with engine.connect() as leader_conn:
        got_lock = False
        while not got_lock:
            got_lock = bool(leader_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LOCK_KEY}).scalar())
            if not got_lock:
                time.sleep(10)
        while True:
            with SessionLocal() as db:
                try:
                    reap_expired(db)
                    schedule_due_jobs(db)
                    db.commit()
                except Exception:
                    db.rollback()
                time.sleep(2)


if __name__ == "__main__":
    main()
