# Contributing

Contributions should keep the implementation small, opt-in, and free of
central-service dependencies. Do not add autonomous discovery, self-install,
unsolicited outreach, hidden persistence, telemetry, payment collection, or
external network calls without a reviewed proposal and explicit operator
consent model.

Before opening a pull request:

1. Explain the trust boundary and any new side effect.
2. Add tests for authorization failures, tampering, replay, concurrency, and
   sensitive-data handling where relevant.
3. Run the commands in `VERIFICATION.md`.
4. Do not include credentials, databases, customer data, real payment data,
   generated secrets, or private screenshots.
5. Preserve the distinction between simulated behavior and provider-verified
   production behavior.

The repository's CI has read-only permissions and runs tests on synthetic
fixtures only. It must not receive deployment or payment credentials.
