import json
import logging
from concurrent.futures import ThreadPoolExecutor
import ast
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch

from agentbridge_reference.adapter import Adapter
from agentbridge_reference.demo import DemoPaymentVerifier, DemoService, run_demo
from agentbridge_reference.errors import BridgeError
from agentbridge_reference.interfaces import PaymentVerification


class Clock:
    def __init__(self, value=2_000_000_000):
        self.value = value
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, seconds):
        with self.lock:
            self.value += seconds


class Service:
    def __init__(self):
        self.quote_calls = 0
        self.fulfillment_calls = 0
        self.lock = threading.Lock()

    def quote(self, item):
        with self.lock:
            self.quote_calls += 1
        if item["sku"] != "TEST-SKU":
            raise ValueError("not found")
        return {"amount_cents": item["quantity"] * 1234, "currency": "usd"}

    def fulfill(self, *, request_id, quote, payment):
        with self.lock:
            self.fulfillment_calls += 1
        return {"delivered": True, "result_reference": "local-test-result"}


class Payment:
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()
        self.reference = "SIMULATED-TEST-REFERENCE"

    def verify(self, reference, amount_cents, currency):
        with self.lock:
            self.calls += 1
        if reference != self.reference:
            raise ValueError("unknown reference")
        return PaymentVerification(
            reference=reference,
            mode="simulated",
            status="simulated_succeeded",
            verified=False,
            amount_cents=amount_cents,
            currency=currency,
        )


def make_system(*, clock=None, actions=None, requests_per_minute=60):
    clock = clock or Clock()
    service = Service()
    payment = Payment()
    adapter = Adapter(
        peers={"agent-alpha": {"token": "local-test-peer-secret", "actions": actions or ["quote", "fulfill"]}},
        operators={"human-operator": {"token": "local-test-operator-secret"}},
        quote_service=service,
        payment_verifier=payment,
        fulfillment_service=service,
        clock=clock,
        approval_key=b"test-only-approval-key-not-for-production",
        requests_per_minute=requests_per_minute,
    )
    return adapter, service, payment, clock


def quote_request(*, request_id="request-001", message_id="message-quote-001", now=2_000_000_000, **overrides):
    message = {
        "type": "request",
        "protocol_version": "0.1",
        "message_id": message_id,
        "request_id": request_id,
        "peer_id": "agent-alpha",
        "action": "quote",
        "item": {"sku": "TEST-SKU", "quantity": 1},
        "idempotency_key": "idempotency-0001",
        "issued_at": now,
        "expires_at": now + 300,
    }
    message.update(overrides)
    return message


def approved_request(adapter, payment, *, request_id="request-001", now=2_000_000_000):
    quote = adapter.quote(
        quote_request(
            request_id=request_id,
            message_id=f"message-quote-{request_id}",
            now=now,
        ),
        "local-test-peer-secret",
    )
    approval = adapter.approve(
        operator_token="local-test-operator-secret",
        request_id=request_id,
        quote_id=quote["quote_id"],
    )
    message = {
        "type": "request",
        "protocol_version": "0.1",
        "message_id": f"message-fulfill-{request_id}",
        "request_id": request_id,
        "peer_id": "agent-alpha",
        "action": "fulfill",
        "approval": approval,
        "payment_status_reference": payment.reference,
        "issued_at": now,
        "expires_at": now + 300,
    }
    return quote, approval, message


class ReferenceAdapterTests(unittest.TestCase):
    def test_valid_quote_approval_simulated_status_and_one_simulated_result(self):
        adapter, service, payment, _ = make_system()
        quote, approval, message = approved_request(adapter, payment)
        result = adapter.fulfill(message, "local-test-peer-secret")
        self.assertEqual(quote["amount_cents"], 1234)
        self.assertEqual(approval["amount_cents"], quote["amount_cents"])
        self.assertEqual(result["status"], "simulated_only")
        self.assertFalse(result["delivered"])
        self.assertFalse(result["payment_verified"])
        self.assertEqual(payment.calls, 1)
        self.assertEqual(service.fulfillment_calls, 0)

    def test_demo_uses_ephemeral_credentials_and_never_delivers(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network used")):
            result = run_demo()
        self.assertIn("SIMULATED", result["label"])
        self.assertEqual(result["payment_status"]["mode"], "simulated")
        self.assertFalse(result["payment_status"]["verified"])
        self.assertFalse(result["fulfillment_result"]["delivered"])
        self.assertEqual(result["real_fulfillment_callback_calls"], 0)
        encoded = json.dumps(result)
        self.assertNotIn("requesting-agent", encoded)
        self.assertNotIn("operator_token", encoded)

    def test_missing_invalid_and_revoked_peer_credentials_are_rejected(self):
        adapter, _, _, _ = make_system()
        message = quote_request()
        for token in (None, "", "wrong-secret"):
            with self.subTest(token=token):
                with self.assertRaises(BridgeError) as caught:
                    adapter.quote(message, token)
                self.assertEqual(caught.exception.code, "unauthorized")
        adapter.quote(message, "local-test-peer-secret")
        adapter.revoke_peer(operator_token="local-test-operator-secret", peer_id="agent-alpha")
        with self.assertRaises(BridgeError) as caught:
            adapter.quote(quote_request(request_id="request-002", message_id="message-quote-002"), "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "unauthorized")

    def test_disallowed_peer_action_and_requester_cannot_self_approve(self):
        adapter, _, _, _ = make_system(actions=["quote"])
        quote = adapter.quote(quote_request(), "local-test-peer-secret")
        approval = adapter.approve(
            operator_token="local-test-operator-secret",
            request_id="request-001",
            quote_id=quote["quote_id"],
        )
        payment = Payment()
        message = {
            "type": "request",
            "protocol_version": "0.1",
            "message_id": "message-fulfill-001",
            "request_id": "request-001",
            "peer_id": "agent-alpha",
            "action": "fulfill",
            "approval": approval,
            "payment_status_reference": payment.reference,
            "issued_at": 2_000_000_000,
            "expires_at": 2_000_000_300,
        }
        with self.assertRaises(BridgeError) as caught:
            adapter.fulfill(message, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "forbidden")

        adapter, _, _, _ = make_system()
        quote = adapter.quote(quote_request(), "local-test-peer-secret")
        with self.assertRaises(BridgeError) as caught:
            adapter.approve(
                operator_token="local-test-peer-secret",
                request_id="request-001",
                quote_id=quote["quote_id"],
            )
        self.assertEqual(caught.exception.code, "unauthorized")

        peer_as_operator = Adapter(
            peers={"agent-alpha": {"token": "same-test-secret-credential", "actions": ["quote", "fulfill"]}},
            operators={"agent-alpha": {"token": "separate-local-test-operator"}},
            quote_service=Service(),
            payment_verifier=Payment(),
            fulfillment_service=Service(),
            clock=Clock(),
            approval_key=b"another-test-only-key",
        )
        quote = peer_as_operator.quote(quote_request(), "same-test-secret-credential")
        with self.assertRaises(BridgeError) as caught:
            peer_as_operator.approve(
                operator_token="separate-local-test-operator",
                request_id="request-001",
                quote_id=quote["quote_id"],
            )
        self.assertEqual(caught.exception.code, "forbidden")

    def test_duplicate_peer_and_operator_credentials_cannot_be_configured(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            Adapter(
                peers={"agent-alpha": {"token": "shared-local-test-secret", "actions": ["quote"]}},
                operators={"human-operator": {"token": "shared-local-test-secret"}},
                quote_service=Service(),
                payment_verifier=Payment(),
                fulfillment_service=Service(),
            )

    def test_operator_cannot_approve_with_wrong_quote_or_expired_quote(self):
        clock = Clock()
        adapter, _, _, _ = make_system(clock=clock)
        quote = adapter.quote(quote_request(now=clock.value), "local-test-peer-secret")
        with self.assertRaises(BridgeError) as caught:
            adapter.approve(
                operator_token="local-test-operator-secret",
                request_id="request-001",
                quote_id="another-quote",
            )
        self.assertEqual(caught.exception.code, "expired")
        clock.advance(301)
        with self.assertRaises(BridgeError) as caught:
            adapter.approve(
                operator_token="local-test-operator-secret",
                request_id="request-001",
                quote_id=quote["quote_id"],
            )
        self.assertEqual(caught.exception.code, "expired")

    def test_approval_tampering_revocation_and_expiry_block_fulfillment(self):
        adapter, _, payment, clock = make_system()
        _, approval, message = approved_request(adapter, payment, now=clock.value)

        for field, value in (("amount_cents", approval["amount_cents"] + 1),
                             ("quote_id", "tampered-quote"),
                             ("currency", "eur")):
            with self.subTest(field=field):
                tampered = json.loads(json.dumps(message))
                tampered["approval"][field] = value
                with self.assertRaises(BridgeError) as caught:
                    adapter.fulfill(tampered, "local-test-peer-secret")
                self.assertEqual(caught.exception.code, "forbidden")

        revoked_adapter, _, revoked_payment, _ = make_system()
        _, revoked_approval, revoked_message = approved_request(revoked_adapter, revoked_payment)
        revoked_adapter.revoke_approval(
            operator_token="local-test-operator-secret",
            approval_id=revoked_approval["approval_id"],
        )
        with self.assertRaises(BridgeError) as caught:
            revoked_adapter.fulfill(revoked_message, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "forbidden")

        clock.advance(301)
        with self.assertRaises(BridgeError) as caught:
            adapter.fulfill(message, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "expired")

    def test_malformed_oversized_unsupported_and_untrusted_destination_messages_fail_closed(self):
        adapter, service, _, _ = make_system()
        malformed = quote_request()
        malformed["callback_url"] = "http://169.254.169.254/latest/meta-data/"
        with self.assertRaises(BridgeError) as caught:
            adapter.quote(malformed, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "invalid_message")
        self.assertEqual(service.quote_calls, 0)

        oversized = quote_request(request_id="request-big", message_id="message-big")
        oversized["item"]["sku"] = "x" * 17_000
        with self.assertRaises(BridgeError) as caught:
            adapter.quote(oversized, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "invalid_message")

        unsupported = quote_request(request_id="request-v2", message_id="message-v2")
        unsupported["protocol_version"] = "99.0"
        with self.assertRaises(BridgeError) as caught:
            adapter.quote(unsupported, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "unsupported_version")
        self.assertEqual(service.quote_calls, 0)

    def test_replayed_message_conflicting_idempotency_and_duplicate_quotes_are_safe(self):
        adapter, service, _, _ = make_system()
        first = quote_request()
        quote = adapter.quote(first, "local-test-peer-secret")
        duplicate = adapter.quote(first, "local-test-peer-secret")
        self.assertEqual(quote, duplicate)
        self.assertEqual(service.quote_calls, 1)

        retry = quote_request(message_id="message-quote-retry")
        self.assertEqual(adapter.quote(retry, "local-test-peer-secret"), quote)
        self.assertEqual(service.quote_calls, 1)

        changed = quote_request(message_id="message-quote-changed")
        changed["item"]["quantity"] = 2
        with self.assertRaises(BridgeError) as caught:
            adapter.quote(changed, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "conflict")

        replay_tamper = quote_request(item={"sku": "OTHER", "quantity": 1})
        with self.assertRaises(BridgeError) as caught:
            adapter.quote(replay_tamper, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "replay")

    def test_concurrent_duplicate_quote_and_fulfillment_do_not_repeat_actions(self):
        adapter, service, payment, clock = make_system()
        original = quote_request(now=clock.value)
        with ThreadPoolExecutor(max_workers=12) as pool:
            quotes = list(pool.map(lambda _: adapter.quote(original, "local-test-peer-secret"), range(12)))
        self.assertEqual(len({quote["quote_id"] for quote in quotes}), 1)
        self.assertEqual(service.quote_calls, 1)

        quote, approval, message = approved_request(adapter, payment, request_id="request-concurrent", now=clock.value)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: adapter.fulfill(message, "local-test-peer-secret"), range(12)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(payment.calls, 1)
        self.assertEqual(service.fulfillment_calls, 0)
        self.assertEqual(results[0]["status"], "simulated_only")

    def test_duplicate_fulfillment_is_cached_and_altered_retry_conflicts(self):
        adapter, service, payment, _ = make_system()
        _, _, message = approved_request(adapter, payment)
        first = adapter.fulfill(message, "local-test-peer-secret")
        self.assertEqual(adapter.fulfill(message, "local-test-peer-secret"), first)
        self.assertEqual(payment.calls, 1)
        self.assertEqual(service.fulfillment_calls, 0)

        changed = json.loads(json.dumps(message))
        changed["message_id"] = "message-fulfill-different"
        changed["payment_status_reference"] = "SIMULATED-OTHER"
        with self.assertRaises(BridgeError) as caught:
            adapter.fulfill(changed, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "conflict")

    def test_unknown_or_forged_payment_reference_never_authorizes_delivery(self):
        adapter, service, payment, _ = make_system()
        _, _, message = approved_request(adapter, payment)
        message["payment_status_reference"] = "SIMULATED-FORGED"
        with self.assertRaises(BridgeError) as caught:
            adapter.fulfill(message, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "payment_unverified")
        self.assertEqual(service.fulfillment_calls, 0)

        message = quote_request(request_id="request-forged-status")
        message["payment_status"] = {"status": "succeeded", "verified": True}
        with self.assertRaises(BridgeError) as caught:
            adapter.quote(message, "local-test-peer-secret")
        self.assertEqual(caught.exception.code, "invalid_message")

    def test_rate_limit_is_enforced_per_peer(self):
        adapter, service, _, clock = make_system(requests_per_minute=1)
        adapter.quote(quote_request(), "local-test-peer-secret")
        with self.assertRaises(BridgeError) as caught:
            adapter.quote(
                quote_request(request_id="request-002", message_id="message-quote-002"),
                "local-test-peer-secret",
            )
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(service.quote_calls, 1)
        clock.advance(61)
        adapter.quote(
            quote_request(request_id="request-002", message_id="message-quote-002", now=clock.value),
            "local-test-peer-secret",
        )
        self.assertEqual(service.quote_calls, 2)

    def test_sensitive_credentials_and_payload_data_are_not_logged(self):
        adapter, _, _, _ = make_system()
        with self.assertLogs("agentbridge_reference", level=logging.INFO) as captured:
            adapter.quote(quote_request(), "local-test-peer-secret")
        logs = "\n".join(captured.output)
        self.assertNotIn("local-test-peer-secret", logs)
        self.assertNotIn("local-test-operator-secret", logs)
        self.assertNotIn("TEST-SKU", logs)
        self.assertNotIn("authorization", logs.lower())

    def test_message_schema_documents_all_required_message_types(self):
        schema_path = Path(__file__).parents[1] / "schemas" / "v0.1" / "messages.schema.json"
        schema = json.loads(schema_path.read_text())
        examples = json.loads((schema_path.parent / "examples.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertTrue({
            "capability", "request", "fulfillmentRequest", "quote", "approval",
            "paymentStatus", "fulfillment", "error",
        }.issubset(schema["$defs"]))
        self.assertTrue({
            "capability", "request", "quote", "approval", "fulfillment_request",
            "payment_status", "fulfillment", "error",
        }.issubset(examples))

    def test_runtime_source_has_no_dynamic_execution_or_network_client(self):
        package = Path(__file__).parents[1] / "agentbridge_reference"
        forbidden_imports = {"requests", "httpx", "urllib", "socket", "subprocess"}
        for source in package.glob("*.py"):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".")[0] for alias in node.names}
                    self.assertFalse(imported & forbidden_imports, source.name)
                elif isinstance(node, ast.ImportFrom):
                    imported = (node.module or "").split(".")[0]
                    self.assertNotIn(imported, forbidden_imports, source.name)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec"}, source.name)


if __name__ == "__main__":
    unittest.main()
