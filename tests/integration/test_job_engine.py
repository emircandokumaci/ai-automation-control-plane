from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import select
from controlplane.db import Base, engine, SessionLocal
from controlplane.models import Project, Workflow, Job
from controlplane.services.jobs import create_job, claim_job, complete_job, ConflictError, StaleLeaseError


@pytest.fixture(autouse=True)
def clean_database():
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


def make_workflow():
    with SessionLocal() as db:
        suffix = uuid4().hex[:8]
        project = Project(slug=f"p-{suffix}", name="Test Project")
        db.add(project); db.flush()
        workflow = Workflow(project_id=project.id, slug="echo", name="Echo", adapter_type="python", required_capability="python", max_concurrency=100, retry_policy={"max_attempts":3})
        db.add(workflow); db.commit()
        return project.id, workflow.id


def test_idempotency_reuses_same_job_and_rejects_payload_change():
    _, workflow_id = make_workflow()
    with SessionLocal() as db:
        one, created1 = create_job(db, workflow_id, {"callable":"system.echo","input":"x"}, idempotency_key="same")
        db.commit(); job_id = one.id
    with SessionLocal() as db:
        two, created2 = create_job(db, workflow_id, {"callable":"system.echo","input":"x"}, idempotency_key="same")
        assert two.id == job_id and created1 and not created2
    with SessionLocal() as db:
        with pytest.raises(ConflictError):
            create_job(db, workflow_id, {"callable":"system.echo","input":"different"}, idempotency_key="same")


def test_parallel_claimers_claim_unique_jobs():
    _, workflow_id = make_workflow()
    with SessionLocal() as db:
        for i in range(40): create_job(db, workflow_id, {"callable":"system.echo","input":i})
        db.commit()
    def claim(worker):
        ids=[]
        while True:
            with SessionLocal() as db:
                item=claim_job(db, worker, ["python"], 60)
                if not item: db.rollback(); break
                job,_=item; ids.append(job.id); db.commit()
        return ids
    with ThreadPoolExecutor(max_workers=8) as ex:
        chunks=list(ex.map(lambda i: claim(f"w-{i}"), range(8)))
    ids=[j for c in chunks for j in c]
    assert len(ids)==40
    assert len(set(ids))==40


def test_fencing_rejects_stale_worker_completion():
    _, workflow_id = make_workflow()
    with SessionLocal() as db:
        job,_=create_job(db, workflow_id, {"callable":"system.echo"}); db.commit(); jid=job.id
    with SessionLocal() as db:
        job,attempt=claim_job(db,"worker-a",["python"],60); token=job.lease_token; db.commit()
    with SessionLocal() as db:
        row=db.get(Job,jid); row.lease_token=token+1; row.worker_id="worker-b"; db.commit()
    with SessionLocal() as db:
        with pytest.raises(StaleLeaseError): complete_job(db,jid,"worker-a",token,receipt_payload={"ok":True},adapter_type="python")
