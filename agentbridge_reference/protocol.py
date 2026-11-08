"""Strict protocol validation and canonical message encoding."""

import json
import re

from .errors import BridgeError

PROTOCOL_VERSION = "0.1"
MAX_MESSAGE_BYTES = 16_384
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PEER_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_CURRENCY = re.compile(r"^[a-z]{3}$")


def canonical_bytes(value: dict) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise BridgeError("invalid_message", "Message must contain valid JSON values.") from None


def validate_common(message: object, expected_type: str) -> dict:
    if not isinstance(message, dict):
        raise BridgeError("invalid_message", "Message must be a JSON object.")
    if len(canonical_bytes(message)) > MAX_MESSAGE_BYTES:
        raise BridgeError("invalid_message", "Message exceeds the size limit.")
    if message.get("protocol_version") != PROTOCOL_VERSION:
        raise BridgeError("unsupported_version", "Unsupported protocol version.")
    if message.get("type") != expected_type:
        raise BridgeError("invalid_message", "Unexpected message type.")
    return message


def _keys(message: dict, required: set[str], optional: set[str] | None = None) -> None:
    optional = optional or set()
    if not required.issubset(message) or set(message) - required - optional:
        raise BridgeError("invalid_message", "Message fields do not match this protocol version.")


def _string(value: object, name: str, *, max_len: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise BridgeError("invalid_message", f"Invalid {name}.")
    return value


def _identifier(value: object, name: str) -> str:
    result = _string(value, name)
    if not _ID.fullmatch(result):
        raise BridgeError("invalid_message", f"Invalid {name}.")
    return result


def _timestamp(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise BridgeError("invalid_message", f"Invalid {name}.")
    return value


def validate_request(message: object) -> dict:
    message = validate_common(message, "request")
    action = message.get("action") if isinstance(message, dict) else None
    if action == "quote":
        fields = {
            "type", "protocol_version", "message_id", "request_id", "peer_id",
            "action", "item", "idempotency_key", "issued_at", "expires_at",
        }
    elif action == "fulfill":
        fields = {
            "type", "protocol_version", "message_id", "request_id", "peer_id",
            "action", "approval", "payment_status_reference", "issued_at", "expires_at",
        }
    else:
        raise BridgeError("invalid_message", "Invalid action.")
    _keys(message, fields)
    _identifier(message["message_id"], "message_id")
    _identifier(message["request_id"], "request_id")
    peer_id = _string(message["peer_id"], "peer_id", max_len=80)
    if not _PEER_ID.fullmatch(peer_id):
        raise BridgeError("invalid_message", "Invalid peer_id.")
    issued_at = _timestamp(message["issued_at"], "issued_at")
    expires_at = _timestamp(message["expires_at"], "expires_at")
    if expires_at <= issued_at:
        raise BridgeError("invalid_message", "Invalid request expiry.")
    if action == "quote":
        item = message["item"]
        if not isinstance(item, dict) or set(item) != {"sku", "quantity"}:
            raise BridgeError("invalid_message", "Item must contain only sku and quantity.")
        _string(item["sku"], "sku", max_len=80)
        if type(item["quantity"]) is not int or not 1 <= item["quantity"] <= 100:
            raise BridgeError("invalid_message", "Invalid item quantity.")
        key = _string(message["idempotency_key"], "idempotency_key")
        if len(key) < 8:
            raise BridgeError("invalid_message", "Invalid idempotency_key.")
    else:
        _string(message["payment_status_reference"], "payment_status_reference", max_len=160)
        approval = message["approval"]
        if not isinstance(approval, dict):
            raise BridgeError("invalid_message", "Approval must be an object.")
        validate_approval(approval)
    return message


def validate_approval(message: object) -> dict:
    message = validate_common(message, "approval")
    fields = {
        "type", "protocol_version", "approval_id", "request_id", "peer_id",
        "action", "quote_id", "amount_cents", "currency", "approver_id",
        "issued_at", "expires_at", "signature",
    }
    _keys(message, fields)
    for field in ("approval_id", "request_id", "quote_id", "approver_id"):
        _identifier(message[field], field)
    _string(message["peer_id"], "peer_id", max_len=80)
    if message["action"] != "fulfill":
        raise BridgeError("invalid_message", "Invalid approval action.")
    if type(message["amount_cents"]) is not int or message["amount_cents"] < 0:
        raise BridgeError("invalid_message", "Invalid approved amount.")
    if not isinstance(message["currency"], str) or not _CURRENCY.fullmatch(message["currency"]):
        raise BridgeError("invalid_message", "Invalid currency.")
    _timestamp(message["issued_at"], "issued_at")
    _timestamp(message["expires_at"], "expires_at")
    if message["expires_at"] <= message["issued_at"]:
        raise BridgeError("invalid_message", "Invalid approval expiry.")
    signature = _string(message["signature"], "signature", max_len=64)
    if not re.fullmatch(r"[a-f0-9]{64}", signature):
        raise BridgeError("invalid_message", "Invalid approval signature.")
    return message


def validate_quote(quote: dict, request_id: str) -> dict:
    if not isinstance(quote, dict) or set(quote) != {
        "type", "protocol_version", "request_id", "quote_id", "sku", "quantity",
        "amount_cents", "currency", "expires_at",
    }:
        raise BridgeError("invalid_message", "Quote service returned an invalid quote.")
    validate_common(quote, "quote")
    if quote["request_id"] != request_id:
        raise BridgeError("invalid_message", "Quote is bound to a different request.")
    _identifier(quote["quote_id"], "quote_id")
    _string(quote["sku"], "sku", max_len=80)
    if type(quote["quantity"]) is not int or not 1 <= quote["quantity"] <= 100:
        raise BridgeError("invalid_message", "Invalid quoted quantity.")
    if type(quote["amount_cents"]) is not int or quote["amount_cents"] < 0:
        raise BridgeError("invalid_message", "Invalid quoted amount.")
    if not isinstance(quote["currency"], str) or not _CURRENCY.fullmatch(quote["currency"]):
        raise BridgeError("invalid_message", "Invalid quote currency.")
    _timestamp(quote["expires_at"], "expires_at")
    return quote


def without_message_id(message: dict) -> dict:
    return {key: value for key, value in message.items() if key != "message_id"}
