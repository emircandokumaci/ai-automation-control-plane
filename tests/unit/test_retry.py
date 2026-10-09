from controlplane.domain.retry import classify_http,should_retry,needs_reconciliation,backoff_seconds
def test_http_classification():
    assert classify_http(429)=='RATE_LIMITED'; assert classify_http(503)=='UPSTREAM_5XX'; assert classify_http(400)=='VALIDATION'
def test_retry_policy(): assert should_retry('RATE_LIMITED'); assert not should_retry('VALIDATION'); assert needs_reconciliation('UNKNOWN_EXTERNAL_STATE')
def test_backoff_is_capped(): assert 0 <= backoff_seconds(20,base=5,cap=10) <= 10
