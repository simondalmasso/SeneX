from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


class UnsafeTargetError(ValueError):
    """Raised when a capture target is not a public HTTP(S) resource."""


_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home")


def _looks_like_legacy_numeric_ipv4(hostname: str) -> bool:
    """Reject non-canonical numeric host spellings accepted by system resolvers."""
    lowered = hostname.lower()
    parts = lowered.split(".")
    if not parts or any(not part for part in parts):
        return False

    def _numeric_part(part: str) -> bool:
        if part.startswith("0x"):
            digits = part[2:]
            return bool(digits) and all(ch in "0123456789abcdef" for ch in digits)
        return part.isdigit()

    return all(_numeric_part(part) for part in parts)


def _validate_ip_literal(hostname: str) -> None:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if _looks_like_legacy_numeric_ipv4(hostname):
            raise UnsafeTargetError("ambiguous numeric network target is forbidden")
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
    if any(ord(ch) > 127 for ch in host):
        raise UnsafeTargetError("non-ASCII hostname is forbidden")
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeTargetError("local hostname is forbidden")
    _validate_ip_literal(host)

    # Drop fragments because they are client-side only and would create duplicate identities.
    normalized = parsed._replace(fragment="")
    return urlunsplit(normalized)
