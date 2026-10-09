# Threat model

Primary risks: credential disclosure, SSRF through generic HTTP, arbitrary shell/container execution, webhook replay, duplicate side effects, stale worker writes, CSRF against owner actions, public database exposure and secret leakage into Git history. Controls include scoped service tokens, host allowlists, no `eval`/`exec`, no generic shell adapter, fencing tokens, CSRF tokens, private Docker networks, Compose secrets and public-repo secret scanning discipline.
