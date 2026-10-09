from __future__ import annotations
import random

RETRYABLE = {"TRANSIENT_NETWORK", "RATE_LIMITED", "UPSTREAM_5XX", "LOST_WORKER"}
RECONCILE = {"TIMEOUT_UNKNOWN_SIDE_EFFECT", "UNKNOWN_EXTERNAL_STATE"}
NON_RETRYABLE = {"AUTH_FORBIDDEN", "VALIDATION", "BUSINESS_RULE", "POISON"}


def classify_http(status_code: int | None = None, exc_name: str | None = None) -> str:
    if exc_name:
        name = exc_name.lower()
        if "timeout" in name:
            return "TRANSIENT_NETWORK"
        if "connect" in name or "network" in name:
            return "TRANSIENT_NETWORK"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code is not None and 500 <= status_code <= 599:
        return "UPSTREAM_5XX"
    if status_code == 401:
        return "AUTH_INVALID"
    if status_code == 403:
        return "AUTH_FORBIDDEN"
    if status_code is not None and 400 <= status_code <= 499:
        return "VALIDATION"
    return "UNKNOWN"


def should_retry(error_class: str) -> bool:
    return error_class in RETRYABLE


def needs_reconciliation(error_class: str) -> bool:
    return error_class in RECONCILE


def backoff_seconds(attempt: int, base: int = 5, cap: int = 900, retry_after: float | None = None) -> float:
    if retry_after is not None and retry_after >= 0:
        return min(float(retry_after), float(cap))
    upper = min(cap, base * (2 ** max(0, attempt - 1)))
    return random.uniform(0, upper)
