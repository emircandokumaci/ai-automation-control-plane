from __future__ import annotations
import json
import secrets
from datetime import datetime, timezone
from typing import Any
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc
from starlette.middleware.sessions import SessionMiddleware
from controlplane.config import SESSION_SECRET, PUBLIC_BASE_URL
from controlplane.db import SessionLocal
from controlplane.models import Project, Workflow, Job, JobAttempt, Event, AuditLog, Worker, Schedule, Receipt, Alert, HealthCheck, ServiceToken, utcnow
from controlplane.security.auth import verify_owner, get_actor, require_session
from controlplane.services.jobs import create_job, request_cancel, redrive_job, audit, event, ConflictError

app = FastAPI(title="AI Automation Control Plane", version="0.1.0", docs_url="/api/docs", redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=PUBLIC_BASE_URL.startswith("https://"), same_site="strict", max_age=60 * 60 * 8)
app.mount("/static", StaticFiles(directory="controlplane/web/static"), name="static")
templates = Jinja2Templates(directory="controlplane/web/templates")


class JobCreate(BaseModel):
    workflow_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    priority: int = 0


class EventIn(BaseModel):
    event_type: str
    source: str = "integration"
    stage: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class N8NEventIn(BaseModel):
    control_plane_job_id: str
    n8n_execution_id: str | None = None
    status: str
    last_node: str | None = None
    error: dict[str, Any] | None = None


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def _require_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(expected, token or ""):
        raise HTTPException(status_code=403, detail="invalid csrf token")


def _ctx(request: Request, **extra):
    return {"request": request, "user": request.session.get("user"), "csrf": _csrf(request), **extra}


def _audit_request(db, request: Request, action: str, object_type: str, object_id: str | None = None, metadata: dict | None = None):
    audit(db, actor_type="user", actor_id=str(request.session.get("user") or "unknown"), action=action, object_type=object_type, object_id=object_id, metadata=metadata, ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"))


@app.get("/healthz")
def healthz():
    try:
        with SessionLocal() as db:
            db.execute(select(func.count()).select_from(Project)).scalar()
        return {"ok": True, "service": "control-plane-api"}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": type(exc).__name__}, status_code=503)


@app.get("/readyz")
def readyz():
    return healthz()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", _ctx(request, error=None))


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form(...)):
    _require_csrf(request, csrf)
    if not verify_owner(username, password):
        return templates.TemplateResponse("login.html", _ctx(request, error="Geçersiz kullanıcı adı veya şifre"), status_code=401)
    request.session.clear()
    request.session["user"] = username
    request.session["csrf"] = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        audit(db, actor_type="user", actor_id=username, action="login", object_type="session", ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"))
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    require_session(request)
    _require_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    require_session(request)
    with SessionLocal() as db:
        statuses = {s: db.scalar(select(func.count()).select_from(Job).where(Job.status == s)) or 0 for s in ["QUEUED", "RUNNING", "RETRY_WAIT", "WAITING_EXTERNAL", "RECONCILING", "FAILED", "DEAD", "SUCCEEDED"]}
        projects = list(db.scalars(select(Project).order_by(Project.name)))
        workers = list(db.scalars(select(Worker).order_by(Worker.id)))
        alerts = list(db.scalars(select(Alert).where(Alert.active.is_(True)).order_by(desc(Alert.created_at)).limit(10)))
        recent = list(db.scalars(select(Job).order_by(desc(Job.created_at)).limit(12)))
        oldest = db.scalar(select(func.min(Job.created_at)).where(Job.status.in_(["QUEUED", "RETRY_WAIT"])))
    oldest_age = int((utcnow() - oldest).total_seconds()) if oldest else 0
    return templates.TemplateResponse("dashboard.html", _ctx(request, statuses=statuses, projects=projects, workers=workers, alerts=alerts, recent=recent, oldest_age=oldest_age))


@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request):
    require_session(request)
    with SessionLocal() as db:
        projects = list(db.scalars(select(Project).order_by(Project.name)))
        workflows = list(db.scalars(select(Workflow).order_by(Workflow.name)))
    return templates.TemplateResponse("projects.html", _ctx(request, projects=projects, workflows=workflows))


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, status: str | None = None):
    require_session(request)
    with SessionLocal() as db:
        q = select(Job).order_by(desc(Job.created_at)).limit(100)
        if status:
            q = select(Job).where(Job.status == status).order_by(desc(Job.created_at)).limit(100)
        jobs = list(db.scalars(q))
        workflows = {w.id: w for w in db.scalars(select(Workflow))}
        projects = {p.id: p for p in db.scalars(select(Project))}
    return templates.TemplateResponse("jobs.html", _ctx(request, jobs=jobs, workflows=workflows, projects=projects, selected_status=status))


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str):
    require_session(request)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404)
        wf = db.get(Workflow, job.workflow_id)
        project = db.get(Project, job.project_id)
        attempts = list(db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_number)))
        events = list(db.scalars(select(Event).where(Event.job_id == job_id).order_by(Event.created_at)))
        receipts = list(db.scalars(select(Receipt).where(Receipt.job_id == job_id).order_by(Receipt.created_at)))
    return templates.TemplateResponse("job_detail.html", _ctx(request, job=job, wf=wf, project=project, attempts=attempts, events=events, receipts=receipts))


@app.post("/jobs/{job_id}/cancel")
def cancel_job_ui(request: Request, job_id: str, csrf: str = Form(...)):
    require_session(request); _require_csrf(request, csrf)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job: raise HTTPException(404)
        request_cancel(db, job)
        event(db, project_id=job.project_id, job_id=job.id, event_type="job.cancel_requested", source="ui")
        _audit_request(db, request, "jobs.cancel", "job", job.id)
        db.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/retry")
def retry_job_ui(request: Request, job_id: str, csrf: str = Form(...)):
    require_session(request); _require_csrf(request, csrf)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job: raise HTTPException(404)
        if job.status not in {"FAILED", "DEAD", "CANCELLED"}:
            raise HTTPException(409, "only terminal failed/dead/cancelled jobs can be redriven")
        new_job = redrive_job(db, job)
        _audit_request(db, request, "jobs.redrive", "job", job.id, {"new_job_id": new_job.id})
        db.commit()
        new_id = new_job.id
    return RedirectResponse(f"/jobs/{new_id}", status_code=303)


@app.get("/failures", response_class=HTMLResponse)
def failures_page(request: Request):
    require_session(request)
    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.status.in_(["FAILED", "DEAD", "RECONCILING"])).order_by(desc(Job.updated_at)).limit(100)))
        workflows = {w.id: w for w in db.scalars(select(Workflow))}
    return templates.TemplateResponse("failures.html", _ctx(request, jobs=jobs, workflows=workflows))


@app.get("/schedules", response_class=HTMLResponse)
def schedules_page(request: Request):
    require_session(request)
    with SessionLocal() as db:
        schedules = list(db.scalars(select(Schedule).order_by(Schedule.next_run_at)))
        workflows = {w.id: w for w in db.scalars(select(Workflow))}
    return templates.TemplateResponse("schedules.html", _ctx(request, schedules=schedules, workflows=workflows))


@app.get("/workers", response_class=HTMLResponse)
def workers_page(request: Request):
    require_session(request)
    with SessionLocal() as db:
        workers = list(db.scalars(select(Worker).order_by(Worker.id)))
    return templates.TemplateResponse("workers.html", _ctx(request, workers=workers, now=utcnow()))


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    require_session(request)
    with SessionLocal() as db:
        rows = list(db.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(200)))
    return templates.TemplateResponse("audit.html", _ctx(request, rows=rows))


@app.get("/api/projects")
def api_projects(request: Request):
    get_actor(request, "jobs:read")
    with SessionLocal() as db:
        return [{"id": p.id, "slug": p.slug, "name": p.name, "status": p.status} for p in db.scalars(select(Project).order_by(Project.name))]


@app.get("/api/jobs")
def api_jobs(request: Request, status: str | None = None):
    get_actor(request, "jobs:read")
    with SessionLocal() as db:
        q = select(Job).order_by(desc(Job.created_at)).limit(200)
        if status: q = select(Job).where(Job.status == status).order_by(desc(Job.created_at)).limit(200)
        return [_job_dict(j) for j in db.scalars(q)]


@app.get("/api/jobs/{job_id}")
def api_job(request: Request, job_id: str):
    get_actor(request, "jobs:read")
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job: raise HTTPException(404)
        return _job_dict(job)


@app.post("/api/jobs")
def api_create_job(request: Request, body: JobCreate):
    actor_type, actor_id = get_actor(request, "jobs:write")
    with SessionLocal() as db:
        try:
            job, created = create_job(db, body.workflow_id, body.payload, idempotency_key=body.idempotency_key, priority=body.priority, source="api")
        except ConflictError as exc:
            raise HTTPException(409, str(exc))
        audit(db, actor_type=actor_type, actor_id=actor_id, action="jobs.create", object_type="job", object_id=job.id, metadata={"created": created})
        db.commit()
        return {"created": created, "job": _job_dict(job)}


@app.post("/api/jobs/{job_id}/events")
def api_job_event(request: Request, job_id: str, body: EventIn):
    actor_type, actor_id = get_actor(request, "events:write")
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job: raise HTTPException(404)
        if body.stage:
            job.stage = body.stage[:120]
        event(db, project_id=job.project_id, job_id=job.id, event_type=body.event_type, source=body.source, payload=body.payload)
        audit(db, actor_type=actor_type, actor_id=actor_id, action="events.write", object_type="job", object_id=job.id, metadata={"event_type": body.event_type})
        db.commit()
    return {"ok": True}


@app.post("/internal/n8n/events")
def n8n_event(request: Request, body: N8NEventIn):
    actor_type, actor_id = get_actor(request, "events:write")
    with SessionLocal() as db:
        job = db.get(Job, body.control_plane_job_id)
        if not job: raise HTTPException(404)
        payload = body.model_dump()
        event(db, project_id=job.project_id, job_id=job.id, event_type=f"n8n.{body.status.lower()}", source="n8n", payload=payload)
        if body.status.lower() == "success" and job.status in {"WAITING_EXTERNAL", "RECONCILING"}:
            job.status = "SUCCEEDED"; job.finished_at = utcnow()
        elif body.status.lower() in {"failed", "error"} and job.status in {"WAITING_EXTERNAL", "RECONCILING"}:
            job.status = "FAILED"; job.finished_at = utcnow(); job.last_error_class = "BUSINESS_RULE"; job.last_error_message = json.dumps(body.error or {})[:4000]
        audit(db, actor_type=actor_type, actor_id=actor_id, action="n8n.callback", object_type="job", object_id=job.id, metadata={"status": body.status})
        db.commit()
    return {"ok": True}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    with SessionLocal() as db:
        counts = {s: db.scalar(select(func.count()).select_from(Job).where(Job.status == s)) or 0 for s in ["QUEUED", "RUNNING", "RETRY_WAIT", "FAILED", "DEAD", "SUCCEEDED"]}
        oldest = db.scalar(select(func.min(Job.created_at)).where(Job.status.in_(["QUEUED", "RETRY_WAIT"])))
        age = max(0, int((utcnow() - oldest).total_seconds())) if oldest else 0
    lines = ["# TYPE controlplane_jobs gauge"]
    for status, count in counts.items():
        lines.append(f'controlplane_jobs{{status="{status}"}} {count}')
    lines.append("# TYPE controlplane_queue_oldest_age_seconds gauge")
    lines.append(f"controlplane_queue_oldest_age_seconds {age}")
    return "\n".join(lines) + "\n"


def _job_dict(j: Job) -> dict:
    return {"id": j.id, "project_id": j.project_id, "workflow_id": j.workflow_id, "status": j.status, "stage": j.stage, "priority": j.priority, "attempt_count": j.attempt_count, "max_attempts": j.max_attempts, "worker_id": j.worker_id, "external_handle": j.external_handle, "last_error_class": j.last_error_class, "last_error_code": j.last_error_code, "last_error_message": j.last_error_message, "created_at": j.created_at.isoformat() if j.created_at else None, "updated_at": j.updated_at.isoformat() if j.updated_at else None}
