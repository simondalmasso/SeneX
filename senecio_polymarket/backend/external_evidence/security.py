from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


class UnsafeTargetError(ValueError):
    """Raised when a capture target is not a public HTTP(S) resource."""


_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home")


def _validate_ip_literal(hostname: str) -> None:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise UnsafeTargetError("private/local/reserved network target is forbidden")


def validate_public_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        raise UnsafeTargetError("target URL is required")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise UnsafeTargetError("target URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeTargetError("only public http/https targets are allowed")
    if not parsed.hostname:
        raise UnsafeTargetError("target hostname is required")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeTargetError("credentials in target URLs are forbidden")

    host = parsed.hostname.rstrip(".").lower()
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeTargetError("local hostname is forbidden")
    _validate_ip_literal(host)

    # Drop fragments because they are client-side only and would create duplicate identities.
    normalized = parsed._replace(fragment="")
    return urlunsplit(normalized)
