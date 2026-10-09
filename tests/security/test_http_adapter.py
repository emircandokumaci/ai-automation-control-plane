from controlplane.adapters.base import ExecutionContext
import controlplane.adapters.http as httpmod


def test_http_adapter_fails_closed_without_allowlist(monkeypatch):
    monkeypatch.setattr(httpmod, "HTTP_ALLOWED_HOSTS", set())
    ctx = ExecutionContext(job_id="j", attempt_id="a", project_id="p", workflow_id="w", idempotency_key=None, timeout=5, lease_token=1, trace_id="t")
    result = httpmod.HTTPAdapter().execute(ctx, {"method": "GET", "url": "https://example.com"})
    assert result.status == "FAILED"
    assert result.error_class == "VALIDATION"
    assert "allowlist" in (result.error_message or "")
