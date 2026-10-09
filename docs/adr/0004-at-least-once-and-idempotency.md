# ADR 0004 — At-least-once delivery with idempotent side effects
Status: Accepted.

The platform does not promise exactly-once execution. It combines durable delivery, idempotency keys, receipts and reconciliation for uncertain external outcomes.
