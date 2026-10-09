# ADR 0005 — Separate control and execution planes
Status: Accepted.

API and scheduler do not execute arbitrary user workloads. Workers receive typed jobs and hold the minimum credentials needed by their adapters.
