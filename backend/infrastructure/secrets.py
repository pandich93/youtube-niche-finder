"""Secrets at rest (plan 14): OAuth refresh tokens and notification secrets
are Fernet-encrypted (AES-128-CBC + HMAC-SHA256) with a key that lives only in
the environment (OWN_TOKENS_KEY), never in the database. Without a valid key
nothing can be stored, so connecting a channel is simply off. Generate a key
once with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Rotation (P1-12): OWN_TOKENS_KEY is the current key -- it encrypts everything
new. OWN_TOKENS_KEY_OLD holds earlier keys, comma-separated, and is only ever
used to read. To rotate: move the current key into OWN_TOKENS_KEY_OLD, put a
fresh one into OWN_TOKENS_KEY, restart, then `cli.py rotate-secrets --apply`
re-encrypts the stored values with the new key; after that the old key can go.
A value no configured key opens is unreadable: decrypt() says so in the log
(which field, never the value) and raises SecretUnreadable, and the admin
summary counts such values. Nothing here logs or returns a plaintext secret.
"""
import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

ENV = "OWN_TOKENS_KEY"
OLD_ENV = "OWN_TOKENS_KEY_OLD"
log = logging.getLogger(__name__)

# what key_state() tells about one stored value
CURRENT, OLD, UNREADABLE = "current", "old", "unreadable"


class SecretsNotConfigured(RuntimeError):
    pass


class SecretUnreadable(SecretsNotConfigured):
    """A stored value that none of the configured keys opens. A subclass, so
    every caller that already handles a missing key handles this too."""


def _key(raw: str, name: str) -> Fernet:
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError):
        raise SecretsNotConfigured(f"{name} is not a valid Fernet key") from None


def _keys() -> list:
    """The current key first, then the read-only old ones."""
    current = os.environ.get(ENV, "").strip()
    if not current:
        raise SecretsNotConfigured(f"{ENV} is not set")
    keys = [_key(current, ENV)]
    for raw in os.environ.get(OLD_ENV, "").split(","):
        if raw.strip():
            keys.append(_key(raw.strip(), OLD_ENV))
    return keys


def _fernet() -> MultiFernet:
    return MultiFernet(_keys())


def configured() -> bool:
    try:
        _fernet()
        return True
    except SecretsNotConfigured:
        return False


def encrypt(plaintext: str) -> bytes:
    """Always with the current key (MultiFernet encrypts with the first)."""
    return _fernet().encrypt(plaintext.encode())


def decrypt(blob: bytes, *, what: str = "secret") -> str:
    """Any configured key reads. `what` names the field for the log line when
    none does -- e.g. "own_channels.token_enc user 3"; never the value."""
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken:
        log.error("stored %s cannot be decrypted with %s or %s: the key was changed or lost "
                  "-- put the old key into %s, or have the user enter it again",
                  what, ENV, OLD_ENV, OLD_ENV)
        raise SecretUnreadable(
            f"stored {what} cannot be read with this {ENV} -- reconnect or re-enter it") from None


def key_state(blob: bytes) -> str:
    """CURRENT, OLD (readable, but only with a key from OWN_TOKENS_KEY_OLD) or
    UNREADABLE. Quiet: the scan behind the admin summary and rotate-secrets
    counts, it does not log every row. Raises SecretsNotConfigured without a
    current key."""
    keys = _keys()
    blob = bytes(blob)
    try:
        keys[0].decrypt(blob)
        return CURRENT
    except InvalidToken:
        pass
    for k in keys[1:]:
        try:
            k.decrypt(blob)
            return OLD
        except InvalidToken:
            continue
    return UNREADABLE


def rotate(blob: bytes) -> bytes:
    """The same plaintext re-encrypted with the current key (the original
    timestamp is kept). Raises SecretUnreadable if no key opens it."""
    try:
        return _fernet().rotate(bytes(blob))
    except InvalidToken:
        raise SecretUnreadable("stored value cannot be read with any configured key") from None
