"""Webhook addresses a signed-in user types in (plan 15, 5.9) must not turn
the server into a proxy into its own network (SSRF): https only, and every
address the host name resolves to must be public -- not loopback, private,
link-local (cloud metadata at 169.254.169.254), multicast or reserved. Checked
when the address is saved AND again right before each send, so a DNS answer
that changes in between does not get through. The installation's own
NOTIFY_WEBHOOK_URL from .env is trusted and not checked.
"""
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeUrl(ValueError):
    pass


def check_public_https(url: str) -> str:
    parts = urlsplit((url or "").strip())
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeUrl("the webhook address must start with https://")
    if parts.username or parts.password:
        raise UnsafeUrl("the webhook address must not contain a user name or password")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeUrl(f"cannot resolve {parts.hostname}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeUrl(f"{parts.hostname} points to a non-public address ({ip})")
    return parts.geturl()
