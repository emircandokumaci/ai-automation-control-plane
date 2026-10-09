# Security

Do not commit credentials, OAuth tokens, `.env` files or runtime secrets. Production secrets are mounted from the local `secrets/` directory, which is gitignored. PostgreSQL, workers and scheduler must not publish public ports. The generic HTTP adapter should use an explicit host allowlist. Arbitrary shell strings, `eval`, `exec` and direct Docker-socket access are intentionally excluded.

Report vulnerabilities privately to the repository owner rather than opening an issue containing secrets.
