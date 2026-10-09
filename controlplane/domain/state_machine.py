from __future__ import annotations

TERMINAL = {"SUCCEEDED", "FAILED", "DEAD", "CANCELLED"}
TRANSITIONS = {
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RETRY_WAIT": {"QUEUED", "RUNNING", "CANCELLED", "DEAD"},
    "RUNNING": {"SUCCEEDED", "WAITING_EXTERNAL", "RETRY_WAIT", "RECONCILING", "FAILED", "DEAD", "CANCEL_REQUESTED"},
    "WAITING_EXTERNAL": {"RUNNING", "RECONCILING", "SUCCEEDED", "RETRY_WAIT", "FAILED", "DEAD", "CANCEL_REQUESTED"},
    "RECONCILING": {"SUCCEEDED", "RETRY_WAIT", "FAILED", "DEAD", "CANCEL_REQUESTED"},
    "CANCEL_REQUESTED": {"CANCELLED", "FAILED", "SUCCEEDED"},
    "SUCCEEDED": set(),
    "FAILED": set(),
    "DEAD": set(),
    "CANCELLED": set(),
}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise ValueError(f"invalid job transition: {current} -> {target}")
