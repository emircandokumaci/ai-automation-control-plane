# ADR 0001 — PostgreSQL as the v1 job queue
Status: Accepted.

Use PostgreSQL `FOR UPDATE SKIP LOCKED` for atomic claims. This keeps jobs, attempts, audit, idempotency and receipts transactional in one durable dependency on the current single-node 2 vCPU / 4 GB deployment. Re-evaluate an external broker only when measured contention, throughput or distributed-worker needs justify it.
