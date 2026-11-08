# AgentBridge Reference

**Experimental reference implementation for opt-in agent-service integration.**

AgentBridge Reference is a small, embeddable example of an authenticated
request → quote → human approval → simulated payment-status check → fulfillment
result flow. It is intended for branded agent services and merchant services
that deliberately integrate it within their own systems.

This repository is an experimental reference, not a hosted service, merchant
installation, payment product, or production-ready integration. Publishing
this code on GitHub makes it available to developers; it does not install it
into any branded service or merchant website. No named brand has integrated,
endorsed, or certified this project.

**Release status: UNRELEASED.** Local verification passed, but the connected
GitHub permission did not allow adding the Actions workflow, so remote CI
could not run. No version tag or GitHub prerelease has been created.

## Quick start

Requirements: Python 3.12 or newer. The demo and tests use only the Python
standard library; there are no runtime dependencies.

```sh
git clone https://github.com/tdfranklin101-ui/agentbridge-reference.git
cd agentbridge-reference
uv sync --locked
python -m compileall -q agentbridge_reference tests
python -m unittest discover -s tests -v
python -m agentbridge_reference.demo
```

If `uv` is not installed, run the same tests with:

```sh
python -m compileall -q agentbridge_reference tests
python -m unittest discover -s tests -v
python -m agentbridge_reference.demo
```

The demo generates temporary requester and operator credentials in memory.
It does not print or persist them. It uses fictional participants, synthetic
catalog data, and a visibly simulated payment reference. It makes no network
request, customer charge, payout, order, delivery, or external service call.

## What is implemented

- A versioned JSON message contract for capability description, request,
  quote, approval, payment-status reference, fulfillment result, and error.
- JSON Schemas and fictional payload examples in `schemas/v0.1/`.
- An embeddable Python adapter with peer authentication, peer/action allowlists,
  operator approval bound to the authenticated requester, request, action,
  quote, amount, currency, and expiry.
- Message-size and version checks, time bounds, in-memory rate limiting,
  replay detection, idempotent duplicate handling, and approval revocation.
- A local requesting-agent demo and a local fulfilling-service demo.
- A host payment-verifier interface. The included implementation is simulation
  only. Simulated payment state can produce only a simulated result and never
  invokes a real fulfillment callback.
- Redacted event logging that excludes credentials and request payloads.

## Architecture

`agentbridge_reference.Adapter` is embedded by the host application. The host
registers configured peer identities, scoped credentials, an operator
credential, a quote service, and a payment-verifier implementation. Requests
carry a configured peer ID, not a callback URL. The adapter does not make
outbound HTTP requests or contact a central AgentBridge server.

The demo uses in-process calls to model the trust boundaries between a
requesting agent, the adapter, an operator, and a fulfilling service. This is
not an MCP server or a network interoperability claim. A host may implement a
transport around these interfaces, but must independently provide transport
authentication, TLS, timeouts, endpoint allowlisting, bounded retries,
durable idempotency, persistence, and production monitoring.

`payment_status` is a result produced by the host's trusted verifier. The
adapter does not accept a requester-supplied `verified` flag or `paid` claim.

## Integration checklist

1. Review `schemas/v0.1/messages.schema.json` and pin the protocol version.
2. Embed the adapter in a service controlled by the operator; do not expose
   credentials in a browser or accept caller-selected destinations.
3. Register only intended peers, capabilities, and actions. Keep requester and
   operator credentials separate, scoped, rotatable, and revocable.
4. Implement the quote callback using the merchant’s authoritative catalog,
   inventory, pricing, and currency rules.
5. Implement human approval in the host and bind it to the exact quote and
   action. Never let a requester approve its own action.
6. Implement a payment verifier that independently checks the provider’s
   authenticated status before any production fulfillment. Do not trust a
   client-supplied `paid` field or a redirect back from checkout.
7. Implement fulfillment and status delivery using the merchant’s own
   idempotency and authorization rules. Simulated statuses must never reach
   a real delivery callback.
8. Add durable replay/idempotency storage, operational alerts, data retention,
   and failure recovery appropriate to your system.
9. Review `SECURITY.md`, test your transport and threat model, and obtain any
   required legal, privacy, merchant, and platform approvals.
10. Revoke credentials and disable the host adapter using the uninstall steps
    in `SECURITY.md`.

## Proposed extensions (not implemented)

Network transports, MCP protocol support, branded-agent connectors, merchant
catalog integrations, persistent approval storage, payment-provider adapters,
refund handling, delivery notifications, and production deployment are not
included. Any such work must be implemented and independently verified by
the host integrator.

## Shutdown and uninstall

The demo is in-memory and stops when its process exits. For an embedding,
remove the adapter from the host, revoke its peer and operator credentials,
disable its routes and callbacks, and remove any host-owned state according to
the operator’s retention policy. No central AgentBridge account or callback
is required.

See `VERIFICATION.md` for commands and the synthetic demo transcript, and
`SECURITY.md` for trust boundaries and limitations. `schemas/v0.1/examples.json`
contains fictional examples; its sample approval signature is deliberately
nonfunctional.
