# Security policy and limitations

This repository is an experimental reference implementation, not a security
certification. Do not expose it to the public internet or use it to authorize
real purchases, payments, or deliveries without a separate security review.

## Trust boundaries

- Peer and operator credentials are configured by the embedding host. The
  adapter compares them in constant time, but does not provide secure storage,
  account recovery, credential rotation, or identity proofing.
- A request is untrusted data. Strict fields and size limits are checked;
  request text is never evaluated as code or instructions.
- An operator credential is separate from a requesting peer credential.
  Approval is cryptographically bound to the peer, request, action, quote,
  amount, currency, and expiry. Approval revocation is enforced in the
  adapter's in-memory state.
- The host owns the catalog, quote, payment-verification, and fulfillment
  callbacks. This repository does not verify a real payment-provider event.
- The demo verifier creates its own synthetic reference and status. It does
  not accept a caller-supplied `paid` claim. Simulated status never calls the
  production fulfillment callback.
- Requests identify configured peer IDs, not URLs. The demo performs no
  outbound networking. A host transport must reject unconfigured destinations
  and must not fetch arbitrary caller-supplied URLs.

## Production host responsibilities

Before any real integration, the host must:

- Use authenticated transport over TLS; bind identities to least-privilege
  capabilities and actions.
- Store credentials and approval/replay state securely, support rotation and
  revocation, and use durable idempotency across process restarts.
- Apply request timeouts, bounded retries, destination allowlists, rate limits,
  monitoring, and incident response at the network boundary.
- Obtain approval from an authorized human or policy engine; never accept
  approval from the requesting peer itself.
- Independently verify payment with the payment provider before fulfillment.
  Validate provider signatures and event/object status using the provider's
  documented flow. A browser redirect, user-supplied reference, or
  client-supplied `paid` field is not payment evidence.
- Make fulfillment idempotent and return truthful status updates to the
  authenticated requester. Implement refund, cancellation, dispute, and
  failure behavior as required by the business.
- Define privacy, retention, merchant authorization, consumer protection, and
  legal/compliance controls for its own deployment.

## Current limitations

The adapter stores state in memory, so restarts lose its replay, approval,
revocation, and idempotency records. It has no network server, MCP support,
database, payment-provider integration, refund system, bank reconciliation,
real fulfillment, or branded-agent compatibility test. Its rate limiter is
process-local. These limitations make it unsuitable for production use as-is.

## Reporting

Do not include credentials, personal data, or private merchant records in bug
reports. No separate security-reporting mailbox or service is designated by
this experimental project. Until an authorized channel is published, report
issues through the repository's private vulnerability-reporting option if it
is available to you.
