from __future__ import annotations
import httpx
class ControlPlaneClient:
    def __init__(self,base_url,token,timeout=3): self.base=base_url.rstrip('/'); self.token=token; self.timeout=timeout
    def event(self,job_id,event_type,stage=None,payload=None):
        r=httpx.post(f"{self.base}/api/jobs/{job_id}/events",headers={"Authorization":f"Bearer {self.token}"},json={"event_type":event_type,"source":"content-engine","stage":stage,"payload":payload or {}},timeout=self.timeout); r.raise_for_status(); return r.json()
