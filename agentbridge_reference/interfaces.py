"""Host-controlled callbacks. No provider or network client is bundled."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PaymentVerification:
    reference: str
    mode: str
    status: str
    verified: bool
    amount_cents: int
    currency: str


class QuoteService(Protocol):
    def quote(self, item: dict) -> dict:
        """Return amount_cents and a lowercase ISO currency code."""


class PaymentVerifier(Protocol):
    def verify(self, reference: str, amount_cents: int, currency: str) -> PaymentVerification:
        """Look up status independently; never trust a caller's paid claim."""


class FulfillmentService(Protocol):
    def fulfill(self, *, request_id: str, quote: dict, payment: PaymentVerification) -> dict:
        """Perform an idempotent host-owned action keyed by request_id."""
