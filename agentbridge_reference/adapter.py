"""In-memory reference adapter with explicit authorization and demo-safe payment handling."""

from collections import defaultdict, deque
from copy import deepcopy
import hashlib
import hmac
import logging
import secrets
import threading
import time

from .errors import BridgeError
from .interfaces import PaymentVerification
from .protocol import (
    canonical_bytes,
    validate_approval,
    validate_quote,
    validate_request,
    without_message_id,
)

logger = logging.getLogger("agentbridge_reference")


class Adapter:
    """Embed this object inside the host service; state is intentionally in-memory.

    Peer configuration maps peer IDs to ``{"token": str, "actions": [str]}``.
    Operator configuration maps operator IDs to ``{"token": str}``. Credentials
    should be generated and stored by the host, never committed to source.
    """

    def __init__(
        self,
        *,
        peers: dict,
        operators: dict,
        quote_service,
        payment_verifier,
        fulfillment_service,
        clock=time.time,
        approval_key: bytes | None = None,
        requests_per_minute: int = 60,
    ):
        if not peers or not operators:
            raise ValueError("At least one peer and one operator must be configured.")
        if not callable(getattr(quote_service, "quote", None)):
            raise ValueError("A host quote service is required.")
        if not callable(getattr(payment_verifier, "verify", None)):
            raise ValueError("A host payment verifier is required.")
        if not callable(getattr(fulfillment_service, "fulfill", None)):
            raise ValueError("A host fulfillment service is required.")
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive.")
        self._peers = {}
        self._operators = {}
        credential_hashes = set()
        for peer_id, config in peers.items():
            if not isinstance(peer_id, str) or not isinstance(config, dict):
                raise ValueError("Peer configuration is invalid.")
            actions = config.get("actions")
            if not isinstance(actions, (list, tuple)) or not actions or not set(actions) <= {"quote", "fulfill"}:
                raise ValueError("Peer actions must be explicitly scoped.")
            token_hash = self._credential_hash(config.get("token"))
            if token_hash in credential_hashes:
                raise ValueError("Peer and operator credentials must be unique.")
            credential_hashes.add(token_hash)
            self._peers[peer_id] = {
                "token_hash": token_hash,
                "actions": set(actions),
                "revoked": bool(config.get("revoked", False)),
            }
        for operator_id, config in operators.items():
            if not isinstance(operator_id, str) or not isinstance(config, dict):
                raise ValueError("Operator configuration is invalid.")
            token_hash = self._credential_hash(config.get("token"))
            if token_hash in credential_hashes:
                raise ValueError("Peer and operator credentials must be unique.")
            credential_hashes.add(token_hash)
            self._operators[operator_id] = {
                "token_hash": token_hash,
                "revoked": bool(config.get("revoked", False)),
            }
        self._quote_service = quote_service
        self._payment_verifier = payment_verifier
        self._fulfillment_service = fulfillment_service
        self._clock = clock
        self._approval_key = approval_key or secrets.token_bytes(32)
        self._requests_per_minute = requests_per_minute
        self._rate_windows = defaultdict(deque)
        self._requests = {}
        self._seen_messages = {}
        self._approvals = {}
        self._fulfillments = {}
        self._lock = threading.RLock()

    def capability(self, service_id: str) -> dict:
        return {
            "type": "capability",
            "protocol_version": "0.1",
            "service_id": service_id,
            "capabilities": ["quote", "human_approval", "payment_status_check", "fulfillment_result"],
            "authentication": {"scheme": "host-configured bearer credential"},
        }

    def quote(self, message: dict, bearer_token: str) -> dict:
        with self._lock:
            peer_id = self._authenticate_peer(message, bearer_token, "quote")
            self._rate_check(peer_id)
            request = validate_request(message)
            if request["action"] != "quote":
                raise BridgeError("invalid_message", "Quote action required.")
            self._validate_time(request)
            full_digest = hashlib.sha256(canonical_bytes(request)).hexdigest()
            replay_key = (peer_id, request["message_id"])
            replay = self._seen_messages.get(replay_key)
            if replay:
                if replay["digest"] != full_digest:
                    raise BridgeError("replay", "Message ID was already used with different content.")
                return deepcopy(replay["response"])

            fingerprint_payload = {
                key: value for key, value in request.items()
                if key not in {"message_id", "issued_at", "expires_at"}
            }
            request_digest = hashlib.sha256(canonical_bytes(fingerprint_payload)).hexdigest()
            existing = self._requests.get(request["request_id"])
            if existing:
                if existing["request_digest"] != request_digest or existing["peer_id"] != peer_id:
                    raise BridgeError("conflict", "Request ID is already bound to different content.")
                response = deepcopy(existing["quote"])
                self._seen_messages[replay_key] = {"digest": full_digest, "response": response}
                return response

            try:
                raw_quote = self._quote_service.quote(deepcopy(request["item"]))
            except Exception:
                raise BridgeError("not_found", "A quote could not be produced.") from None
            if not isinstance(raw_quote, dict) or set(raw_quote) != {"amount_cents", "currency"}:
                raise BridgeError("invalid_message", "Quote service returned invalid fields.")
            quote = {
                "type": "quote",
                "protocol_version": "0.1",
                "request_id": request["request_id"],
                "quote_id": secrets.token_hex(12),
                "sku": request["item"]["sku"],
                "quantity": request["item"]["quantity"],
                "amount_cents": raw_quote["amount_cents"],
                "currency": raw_quote["currency"],
                "expires_at": min(request["expires_at"], int(self._clock()) + 300),
            }
            validate_quote(quote, request["request_id"])
            self._requests[request["request_id"]] = {
                "peer_id": peer_id,
                "request_digest": request_digest,
                "request": deepcopy(request),
                "quote": deepcopy(quote),
            }
            self._seen_messages[replay_key] = {"digest": full_digest, "response": deepcopy(quote)}
            logger.info("quote_created peer=%s request=%s", peer_id, request["request_id"])
            return quote

    def approve(
        self,
        *,
        operator_token: str,
        request_id: str,
        quote_id: str,
        action: str = "fulfill",
        ttl_seconds: int = 300,
    ) -> dict:
        with self._lock:
            operator_id = self._authenticate_operator(operator_token)
            if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 300:
                raise BridgeError("invalid_message", "Approval lifetime must be 1–300 seconds.")
            if action != "fulfill":
                raise BridgeError("forbidden", "This operator is not approving that action.")
            record = self._requests.get(request_id)
            if not record:
                raise BridgeError("not_found", "Request was not found.")
            peer = self._peers.get(record["peer_id"])
            if not peer or peer.get("revoked"):
                raise BridgeError("unauthorized", "Requesting peer is revoked.")
            if operator_id == record["peer_id"]:
                raise BridgeError("forbidden", "A requesting peer cannot approve its own action.")
            quote = record["quote"]
            now = int(self._clock())
            if quote["expires_at"] <= now or quote_id != quote["quote_id"]:
                raise BridgeError("expired", "Quote is expired or does not match.")
            approval = {
                "type": "approval",
                "protocol_version": "0.1",
                "approval_id": secrets.token_hex(16),
                "request_id": request_id,
                "peer_id": record["peer_id"],
                "action": action,
                "quote_id": quote["quote_id"],
                "amount_cents": quote["amount_cents"],
                "currency": quote["currency"],
                "approver_id": operator_id,
                "issued_at": now,
                "expires_at": min(now + ttl_seconds, quote["expires_at"]),
            }
            approval["signature"] = self._sign_approval(approval)
            validate_approval(approval)
            self._approvals[approval["approval_id"]] = {
                "approval": deepcopy(approval),
                "revoked": False,
            }
            logger.info("approval_issued operator=%s request=%s", operator_id, request_id)
            return approval

    def fulfill(self, message: dict, bearer_token: str) -> dict:
        with self._lock:
            peer_id = self._authenticate_peer(message, bearer_token, "fulfill")
            self._rate_check(peer_id)
            request = validate_request(message)
            if request["action"] != "fulfill":
                raise BridgeError("invalid_message", "Fulfill action required.")
            self._validate_time(request)
            full_digest = hashlib.sha256(canonical_bytes(request)).hexdigest()
            replay_key = (peer_id, request["message_id"])
            replay = self._seen_messages.get(replay_key)
            if replay:
                if replay["digest"] != full_digest:
                    raise BridgeError("replay", "Message ID was already used with different content.")
                return deepcopy(replay["response"])

            prior = self._fulfillments.get(request["request_id"])
            fingerprint = hashlib.sha256(
                canonical_bytes({
                    key: value for key, value in request.items()
                    if key not in {"message_id", "issued_at", "expires_at"}
                })
            ).hexdigest()
            if prior:
                if prior["fingerprint"] != fingerprint or prior["peer_id"] != peer_id:
                    raise BridgeError("conflict", "Request already has a different fulfillment attempt.")
                response = deepcopy(prior["result"])
                self._seen_messages[replay_key] = {"digest": full_digest, "response": response}
                return response

            record = self._requests.get(request["request_id"])
            if not record or record["peer_id"] != peer_id:
                raise BridgeError("not_found", "Approved request was not found.")
            peer = self._peers.get(peer_id)
            if not peer or peer.get("revoked"):
                raise BridgeError("unauthorized", "Requesting peer is revoked.")
            approval = validate_approval(request["approval"])
            self._verify_approval(approval, record)
            if record["quote"]["expires_at"] <= int(self._clock()):
                raise BridgeError("expired", "Quote has expired.")

            try:
                payment = self._payment_verifier.verify(
                    request["payment_status_reference"],
                    record["quote"]["amount_cents"],
                    record["quote"]["currency"],
                )
            except Exception:
                raise BridgeError("payment_unverified", "Payment verification failed.") from None
            if not isinstance(payment, PaymentVerification):
                raise BridgeError("payment_unverified", "Payment verifier returned an invalid result.")
            if (
                payment.reference != request["payment_status_reference"]
                or payment.amount_cents != record["quote"]["amount_cents"]
                or payment.currency != record["quote"]["currency"]
            ):
                raise BridgeError("payment_unverified", "Payment does not match the approved quote.")

            if payment.mode == "simulated":
                if payment.status != "simulated_succeeded" or payment.verified:
                    raise BridgeError("payment_unverified", "Invalid simulated payment status.")
                result = {
                    "type": "fulfillment",
                    "protocol_version": "0.1",
                    "request_id": request["request_id"],
                    "approval_id": approval["approval_id"],
                    "status": "simulated_only",
                    "delivered": False,
                    "payment_mode": "simulated",
                    "payment_verified": False,
                    "result_reference": payment.reference,
                }
            elif payment.mode == "provider_verified" and payment.status == "succeeded" and payment.verified:
                try:
                    outcome = self._fulfillment_service.fulfill(
                        request_id=request["request_id"],
                        quote=deepcopy(record["quote"]),
                        payment=payment,
                    )
                except Exception:
                    raise BridgeError(
                        "conflict", "Fulfillment result could not be confirmed.", retryable=True
                    ) from None
                if not isinstance(outcome, dict) or not isinstance(outcome.get("result_reference"), str):
                    raise BridgeError("invalid_message", "Fulfillment service returned an invalid result.")
                result = {
                    "type": "fulfillment",
                    "protocol_version": "0.1",
                    "request_id": request["request_id"],
                    "approval_id": approval["approval_id"],
                    "status": "fulfilled",
                    "delivered": outcome.get("delivered") is True,
                    "payment_mode": "provider_verified",
                    "payment_verified": True,
                    "result_reference": outcome["result_reference"][:160],
                }
            else:
                raise BridgeError("payment_unverified", "Payment is not independently verified as successful.")

            self._fulfillments[request["request_id"]] = {
                "fingerprint": fingerprint,
                "peer_id": peer_id,
                "result": deepcopy(result),
            }
            self._seen_messages[replay_key] = {"digest": full_digest, "response": deepcopy(result)}
            logger.info("fulfillment_result request=%s status=%s", request["request_id"], result["status"])
            return result

    def revoke_approval(self, *, operator_token: str, approval_id: str) -> None:
        with self._lock:
            self._authenticate_operator(operator_token)
            record = self._approvals.get(approval_id)
            if not record:
                raise BridgeError("not_found", "Approval was not found.")
            record["revoked"] = True

    def revoke_peer(self, *, operator_token: str, peer_id: str) -> None:
        with self._lock:
            self._authenticate_operator(operator_token)
            peer = self._peers.get(peer_id)
            if not peer:
                raise BridgeError("not_found", "Peer was not found.")
            peer["revoked"] = True

    def _authenticate_peer(self, message: object, token: str, action: str) -> str:
        peer_id = message.get("peer_id") if isinstance(message, dict) else None
        peer = self._peers.get(peer_id) if isinstance(peer_id, str) else None
        if (
            not peer
            or peer.get("revoked")
            or not isinstance(token, str)
            or len(token) < 16
        ):
            raise BridgeError("unauthorized", "Valid peer authentication is required.")
        if not hmac.compare_digest(peer["token_hash"], self._credential_hash(token)):
            raise BridgeError("unauthorized", "Valid peer authentication is required.")
        if action not in peer.get("actions", []):
            raise BridgeError("forbidden", "Peer is not permitted to perform this action.")
        return peer_id

    def _authenticate_operator(self, token: str) -> str:
        if not isinstance(token, str) or len(token) < 16:
            raise BridgeError("unauthorized", "Valid operator authentication is required.")
        for operator_id, operator in self._operators.items():
            if (
                not operator.get("revoked")
                and hmac.compare_digest(operator["token_hash"], self._credential_hash(token))
            ):
                return operator_id
        raise BridgeError("unauthorized", "Valid operator authentication is required.")

    @staticmethod
    def _credential_hash(token: object) -> bytes:
        if not isinstance(token, str) or len(token) < 16:
            raise ValueError("Configured credentials must be at least 16 characters.")
        return hashlib.sha256(token.encode("utf-8")).digest()

    def _validate_time(self, message: dict) -> None:
        now = int(self._clock())
        if message["issued_at"] > now + 30 or message["issued_at"] < now - 300:
            raise BridgeError("expired", "Request timestamp is outside the accepted window.")
        if message["expires_at"] <= now or message["expires_at"] > now + 600:
            raise BridgeError("expired", "Request is expired or has an excessive lifetime.")

    def _rate_check(self, peer_id: str) -> None:
        now = self._clock()
        window = self._rate_windows[peer_id]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= self._requests_per_minute:
            raise BridgeError("rate_limited", "Peer request rate limit exceeded.", retryable=True)
        window.append(now)

    def _sign_approval(self, approval: dict) -> str:
        unsigned = {key: value for key, value in approval.items() if key != "signature"}
        return hmac.new(self._approval_key, canonical_bytes(unsigned), hashlib.sha256).hexdigest()

    def _verify_approval(self, approval: dict, request_record: dict) -> None:
        record = self._approvals.get(approval["approval_id"])
        expected = self._sign_approval(approval)
        if not hmac.compare_digest(expected, approval["signature"]):
            raise BridgeError("forbidden", "Approval signature is invalid.")
        if not record or record["revoked"] or canonical_bytes(record["approval"]) != canonical_bytes(approval):
            raise BridgeError("forbidden", "Approval is revoked or unknown.")
        quote = request_record["quote"]
        if (
            approval["request_id"] != request_record["request"]["request_id"]
            or approval["peer_id"] != request_record["peer_id"]
            or approval["action"] != "fulfill"
            or approval["quote_id"] != quote["quote_id"]
            or approval["amount_cents"] != quote["amount_cents"]
            or approval["currency"] != quote["currency"]
            or approval["expires_at"] <= int(self._clock())
            or approval["approver_id"] == approval["peer_id"]
        ):
            raise BridgeError("forbidden", "Approval does not match this request and quote.")
