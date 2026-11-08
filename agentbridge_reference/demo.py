"""Run the local, in-memory demonstration. No credentials or effects persist."""

from copy import deepcopy
import json
import secrets
import time

from .adapter import Adapter
from .interfaces import PaymentVerification


class DemoService:
    def __init__(self):
        self.fulfillment_calls = 0

    def quote(self, item: dict) -> dict:
        if item["sku"] != "DEMO-ITEM":
            raise ValueError("Unknown synthetic demo item.")
        return {"amount_cents": 1250 * item["quantity"], "currency": "usd"}

    def fulfill(self, *, request_id: str, quote: dict, payment: PaymentVerification) -> dict:
        # The simulated branch never calls this host callback.
        self.fulfillment_calls += 1
        return {"delivered": False, "result_reference": "unexpected-demo-callback"}


class DemoPaymentVerifier:
    """Creates its own simulation references; callers cannot set payment status."""

    def __init__(self):
        self._references = {}

    def issue_simulated_reference(self, *, amount_cents: int, currency: str) -> str:
        reference = "SIMULATED-" + secrets.token_urlsafe(16)
        self._references[reference] = (amount_cents, currency)
        return reference

    def verify(self, reference: str, amount_cents: int, currency: str) -> PaymentVerification:
        recorded = self._references.get(reference)
        if recorded != (amount_cents, currency):
            raise ValueError("Unknown or mismatched simulated reference.")
        return PaymentVerification(
            reference=reference,
            mode="simulated",
            status="simulated_succeeded",
            verified=False,
            amount_cents=amount_cents,
            currency=currency,
        )


class DemoRequester:
    """A separate fictional requesting service that authenticates to the adapter."""

    def __init__(self, *, adapter: Adapter, peer_id: str, credential: str):
        self._adapter = adapter
        self._peer_id = peer_id
        self._credential = credential

    def request_quote(self, *, request_id: str, sku: str, quantity: int, issued_at: int) -> dict:
        return self._adapter.quote(
            {
                "type": "request",
                "protocol_version": "0.1",
                "message_id": "demo-message-quote-001",
                "request_id": request_id,
                "peer_id": self._peer_id,
                "action": "quote",
                "item": {"sku": sku, "quantity": quantity},
                "idempotency_key": "demo-idempotency-001",
                "issued_at": issued_at,
                "expires_at": issued_at + 300,
            },
            self._credential,
        )

    def request_fulfillment(
        self, *, request_id: str, approval: dict, payment_reference: str, issued_at: int
    ) -> dict:
        return self._adapter.fulfill(
            {
                "type": "request",
                "protocol_version": "0.1",
                "message_id": "demo-message-fulfill-001",
                "request_id": request_id,
                "peer_id": self._peer_id,
                "action": "fulfill",
                "approval": deepcopy(approval),
                "payment_status_reference": payment_reference,
                "issued_at": issued_at,
                "expires_at": issued_at + 300,
            },
            self._credential,
        )


def run_demo() -> dict:
    peer_token = secrets.token_urlsafe(32)
    operator_token = secrets.token_urlsafe(32)
    service = DemoService()
    payment_verifier = DemoPaymentVerifier()
    adapter = Adapter(
        peers={"requesting-agent": {"token": peer_token, "actions": ["quote", "fulfill"]}},
        operators={"local-operator": {"token": operator_token}},
        quote_service=service,
        payment_verifier=payment_verifier,
        fulfillment_service=service,
    )
    requesting_agent = DemoRequester(
        adapter=adapter,
        peer_id="requesting-agent",
        credential=peer_token,
    )
    now = int(time.time())
    quote = requesting_agent.request_quote(
        request_id="demo-request-001",
        sku="DEMO-ITEM",
        quantity=1,
        issued_at=now,
    )
    approval = adapter.approve(
        operator_token=operator_token,
        request_id="demo-request-001",
        quote_id=quote["quote_id"],
    )
    payment_reference = payment_verifier.issue_simulated_reference(
        amount_cents=quote["amount_cents"], currency=quote["currency"]
    )
    result = requesting_agent.request_fulfillment(
        request_id="demo-request-001",
        approval=approval,
        payment_reference=payment_reference,
        issued_at=int(time.time()),
    )
    return {
        "label": "SIMULATED DEMONSTRATION — not a purchase, payment, or delivery",
        "request": {"request_id": "demo-request-001", "sku": "DEMO-ITEM", "quantity": 1},
        "quote": {"amount_cents": quote["amount_cents"], "currency": quote["currency"]},
        "approval": {"approver_id": approval["approver_id"], "action": approval["action"]},
        "payment_status": {
            "reference": payment_reference,
            "mode": "simulated",
            "status": "simulated_succeeded",
            "verified": False,
        },
        "fulfillment_result": result,
        "real_fulfillment_callback_calls": service.fulfillment_calls,
    }


def main() -> None:
    print(json.dumps(run_demo(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
