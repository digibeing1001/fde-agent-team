# Security Policy

## Supported branch

Security fixes are applied to `main`. Pin deployments to a reviewed commit; do not execute unreviewed role cards, state snapshots, tool schemas, or imported skills from a mutable branch.

## Report a vulnerability

Use GitHub private vulnerability reporting or a private security advisory for this repository. Do not put credentials, private prompts, tenant data, exploit payloads, or production state files in a public issue. If private reporting is unavailable, open a minimal public issue asking the maintainer for a private channel without disclosing the vulnerability.

Include the affected commit, host adapter, trust boundary, reproduction steps with synthetic data, expected impact, and whether the issue can trigger an external side effect.

## Runtime trust boundaries

- Treat Agent output, tool output, retrieved documents, state snapshots, and handoff artifacts as untrusted input.
- The Lead tool allowlist and gate checks must be enforced in code. Prompt text is not an authorization boundary.
- Use `AtomicJsonStateStore` or an adapter with equivalent atomic compare-and-set behavior for concurrent workers. A plain in-memory dictionary is test-only.
- Give every external side effect a stable idempotency key and require approval for irreversible, privileged, legal, financial, publication, or customer-facing actions.
- Keep credentials out of prompts, transition logs, artifacts, and repository files. Inject them through the host secret store at execution time.
- Isolate tenants and projects at both path and authorization layers. A `project_id` label alone is not access control.
- Validate imported skills and adapters before enabling them; pin their source and version, review executable scripts, and maintain a rollback path.

## Known scope limits

The bundled JSON store provides single-host durability. It does not provide distributed consensus, network isolation, a tool sandbox, secret management, or exactly-once semantics for third-party APIs. Production clusters must replace it with a transactional backend and preserve the same state-transition, idempotency, and event-ledger contract.
