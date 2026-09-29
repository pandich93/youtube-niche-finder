"""Secrets at rest (plan 14): OAuth refresh tokens are Fernet-encrypted
(AES-128-CBC + HMAC-SHA256) with a key that lives only in the environment
(OWN_TOKENS_KEY), never in the database. Without a valid key nothing can be
stored, so connecting a channel is simply off. Generate a key once with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Losing or changing the key makes stored tokens unreadable: reconnect the
channel. Nothing here logs or returns a plaintext token.
"""
import os

from cryptography.fernet import Fernet, InvalidToken

ENV = "OWN_TOKENS_KEY"


class SecretsNotConfigured(RuntimeError):
    pass


def _fernet():
    key = os.environ.get(ENV, "").strip()
    if not key:
        raise SecretsNotConfigured(f"{ENV} is not set")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise SecretsNotConfigured(f"{ENV} is not a valid Fernet key") from None


def configured() -> bool:
    try:
        _fernet()
        return True
    except SecretsNotConfigured:
        return False


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(blob: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken:
        raise SecretsNotConfigured(
            f"stored token cannot be read with this {ENV} -- reconnect the channel") from None
