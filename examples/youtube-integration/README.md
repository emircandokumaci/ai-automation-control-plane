# YouTube integration

The content engine remains the owner of script, visual-provider fallback, rendering, QC and upload implementation. The Control Plane owns durable orchestration state, retry budgets, audit, receipts and reconciliation. Instrument stages with `scripts/cp_client.py`; do not couple the Control Plane directly to private content-engine databases.
