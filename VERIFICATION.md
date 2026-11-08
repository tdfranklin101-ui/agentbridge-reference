# Verification record

This record applies only to the source in this repository. All test data and
payment references are synthetic.

## Commands

From a clean checkout, with Python 3.12+ and uv installed:

```sh
uv sync --locked
python -m compileall -q agentbridge_reference tests
python -m unittest discover -s tests -v
python -m agentbridge_reference.demo
```

The unittest suite covers request-to-quote, operator-bound approval,
simulation-only payment verification and result; missing/invalid/revoked
credentials; peer/action restrictions and self-approval; expiry and
revocation; quote and approval tampering; malformed, oversized, and
unsupported-version messages; arbitrary destination rejection; replay and
idempotency; concurrent duplicates; simulated-payment forgery; rate limiting;
credential uniqueness; runtime import/dynamic-execution guardrails; and log
redaction.

## Latest local result

- Runtime: Python 3.12.12.
- Package manager: uv 0.13.4.
- Tests: 16 passed, 0 failed, 0 skipped in both the release tree and a fresh
  local clone.
- Compile check: passed.
- JSON syntax check: passed.
- Network-free demonstration: passed. It returned one `simulated_only` result,
  `delivered=false`, `payment_verified=false`, and zero calls to the real
  fulfillment callback.
- Dependency set: no runtime or development third-party packages are declared.
- Release secret-pattern scan: 0 candidate matches across the 20-file
  allowlist, including `.env.example` and both schemas.
- Targeted Semgrep scan: 0 findings across 6 runtime Python files using 3
  local rules for dynamic execution, shell commands, and known network clients.
- Clean-clone demo: passed from a fresh clone of the isolated release Git
  repository; clone working tree remained clean after verification.
- CI workflow: YAML validated, actions pinned to full commit SHAs, and
  repository/job permissions restricted to `contents: read`.
- Lint/type checks: no linter or type checker is configured; bytecode compile
  and behavioral tests are the available checks.

## Synthetic demonstration transcript

```json
{
  "label": "SIMULATED DEMONSTRATION — not a purchase, payment, or delivery",
  "request": {
    "request_id": "demo-request-001",
    "sku": "DEMO-ITEM",
    "quantity": 1
  },
  "quote": {
    "amount_cents": 1250,
    "currency": "usd"
  },
  "approval": {
    "approver_id": "local-operator",
    "action": "fulfill"
  },
  "payment_status": {
    "reference": "SIMULATED-<ephemeral>",
    "mode": "simulated",
    "status": "simulated_succeeded",
    "verified": false
  },
  "fulfillment_result": {
    "status": "simulated_only",
    "delivered": false,
    "payment_mode": "simulated",
    "payment_verified": false
  },
  "real_fulfillment_callback_calls": 0
}
```

## Limitations and scan scope

The adapter is in-memory and has no transport server, persistent state,
payment-provider integration, refund handling, or real fulfillment. The
simulation is not proof of payment or real branded-agent compatibility.
Production transport, TLS, timeouts, bounded retries, endpoint restrictions,
durable idempotency, payment-provider signature verification, and fulfillment
must be implemented and tested by the host.

The release declares no third-party runtime or development dependencies, so
there are no third-party package licenses or dependency advisories in the
release dependency graph. The local release source and secret scans and
clean-clone test passed. GitHub Actions could not run because the connected
authorization cannot add `.github/workflows/tests.yml`; the repository is
explicitly marked unreleased. Do not create a version tag until remote CI and
an independent public clone pass.
