"""Receiver authentication with constant-time token comparison."""

from __future__ import annotations

import hmac


class AuthenticationError(Exception):
    """Raised when a receiver cannot be authenticated."""


class ReceiverAuthenticator:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def authenticate(self, receiver_id: str, token: str) -> None:
        expected = self._tokens.get(receiver_id)
        if expected is None or not hmac.compare_digest(expected, token):
            raise AuthenticationError("invalid receiver credentials")

    @staticmethod
    def object_prefix(receiver_id: str) -> str:
        return f"receivers/{receiver_id}/"
