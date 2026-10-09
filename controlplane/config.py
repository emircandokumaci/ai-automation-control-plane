from __future__ import annotations
import os
from pathlib import Path
from urllib.parse import quote_plus

BASE_DIR = Path(__file__).resolve().parents[1]


def _read_secret(name: str, default: str = "") -> str:
    direct = os.getenv(name)
    if direct:
        return direct.strip()
    path = os.getenv(f"{name}_FILE", f"/run/secrets/{name.lower()}")
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


DB_PASSWORD = _read_secret("DB_PASSWORD", "controlplane")
DB_HOST = os.getenv("DB_HOST", "postgres")
DATABASE_URL = os.getenv("DATABASE_URL") or f"postgresql+psycopg://controlplane:{quote_plus(DB_PASSWORD)}@{DB_HOST}:5432/controlplane"
SESSION_SECRET = _read_secret("SESSION_SECRET", "dev-only-change-me")
OWNER_PASSWORD = _read_secret("OWNER_PASSWORD", "")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "owner")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8088")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
WORKER_ID = os.getenv("WORKER_ID", os.uname().nodename)
WORKER_CAPABILITIES = [x.strip() for x in os.getenv("WORKER_CAPABILITIES", "python,http,n8n").split(",") if x.strip()]
WORKER_CONCURRENCY = max(1, int(os.getenv("WORKER_CONCURRENCY", "2")))
LEASE_SECONDS = max(15, int(os.getenv("LEASE_SECONDS", "60")))
HEARTBEAT_SECONDS = max(5, int(os.getenv("HEARTBEAT_SECONDS", "15")))
POLL_SECONDS = max(0.2, float(os.getenv("POLL_SECONDS", "1")))
HTTP_ALLOWED_HOSTS = {x.strip().lower() for x in os.getenv("HTTP_ALLOWED_HOSTS", "").split(",") if x.strip()}
YOUTUBE_HEALTH_FILE = os.getenv("YOUTUBE_HEALTH_FILE", "/integrations/youtube/health.json")
BINANCE_HEALTH_URL = os.getenv("BINANCE_HEALTH_URL", "http://host.docker.internal:8091/health")
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://host.docker.internal:5678")
SCHEDULER_LOCK_KEY = int(os.getenv("SCHEDULER_LOCK_KEY", "741852963"))
