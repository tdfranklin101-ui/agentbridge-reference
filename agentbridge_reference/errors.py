"""Safe, transport-neutral errors; messages never include credential values."""


class BridgeError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_message(self) -> dict:
        return {
            "type": "error",
            "protocol_version": "0.1",
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
