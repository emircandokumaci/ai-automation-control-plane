from __future__ import annotations
import json, logging, sys
SENSITIVE={"authorization","access_token","refresh_token","api_key","secret","password"}
def redact(value):
    if isinstance(value,dict): return {k:("[REDACTED]" if k.lower() in SENSITIVE else redact(v)) for k,v in value.items()}
    if isinstance(value,list): return [redact(v) for v in value]
    return value
class JsonFormatter(logging.Formatter):
    def format(self,record):
        data={"level":record.levelname,"logger":record.name,"message":record.getMessage()}
        for k in ("service","project_id","workflow_id","job_id","attempt_id","trace_id","adapter","event"):
            if hasattr(record,k): data[k]=getattr(record,k)
        return json.dumps(redact(data),ensure_ascii=False)
def configure(level="INFO"):
    h=logging.StreamHandler(sys.stdout); h.setFormatter(JsonFormatter())
    root=logging.getLogger(); root.handlers=[h]; root.setLevel(level)
