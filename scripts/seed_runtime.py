#!/usr/bin/env python3
from datetime import datetime,timezone
from croniter import croniter
from zoneinfo import ZoneInfo
from sqlalchemy import select
from controlplane.db import SessionLocal
from controlplane.models import Project,Workflow,Schedule
def next_utc(expr,tz):
    local=datetime.now(ZoneInfo(tz)); return croniter(expr,local).get_next(datetime).astimezone(timezone.utc)
with SessionLocal() as db:
    def project(slug,name):
        p=db.scalar(select(Project).where(Project.slug==slug))
        if not p: p=Project(slug=slug,name=name); db.add(p); db.flush()
        return p
    def workflow(p,slug,name,callable_name):
        w=db.scalar(select(Workflow).where(Workflow.project_id==p.id,Workflow.slug==slug,Workflow.version==1))
        if not w: w=Workflow(project_id=p.id,slug=slug,name=name,adapter_type='python',required_capability='python',max_concurrency=1,timeout_seconds=20,retry_policy={"max_attempts":3,"base_seconds":5,"cap_seconds":300},config={},input_schema={}); db.add(w); db.flush()
        return w
    yt=project('ai-content-automation','AI Content Automation Engine'); bn=project('binance-monitoring','Binance Futures Engine · Monitoring'); core=project('control-plane-demo','Control Plane Reliability Demo')
    wyt=workflow(yt,'youtube-health','YouTube Pipeline Health','youtube.health'); wbn=workflow(bn,'binance-health','Binance Engine Health','binance.health'); wdemo=workflow(core,'echo','Deterministic Echo','system.echo')
    for w,expr,tz in [(wyt,'*/15 * * * *','Europe/Istanbul'),(wbn,'*/10 * * * *','Europe/Istanbul')]:
        s=db.scalar(select(Schedule).where(Schedule.workflow_id==w.id))
        if not s: db.add(Schedule(workflow_id=w.id,cron_expression=expr,timezone=tz,next_run_at=next_utc(expr,tz),misfire_policy='RUN_ONCE',catch_up_limit=1,enabled=True))
    # Store callable defaults in workflow config; API callers can create jobs with these payloads.
    wyt.config={"default_payload":{"callable":"youtube.health"}}; wbn.config={"default_payload":{"callable":"binance.health"}}; wdemo.config={"default_payload":{"callable":"system.echo","input":"hello"}}
    db.commit()
print('seeded')
