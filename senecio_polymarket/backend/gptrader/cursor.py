from __future__ import annotations

import base64
from dataclasses import dataclass

_CURSOR_PREFIX = "gptrader.v1:"


class CursorError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class PacketCursor:
    packet_seq: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.packet_seq, bool) or not isinstance(self.packet_seq, int) or self.packet_seq < 0:
            raise CursorError("packet_seq must be a non-negative integer")

    @property
    def token(self) -> str:
        raw = f"{_CURSOR_PREFIX}{self.packet_seq}".encode("ascii")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def from_token(cls, token: str | None) -> "PacketCursor":
        if token in (None, ""):
            return cls(0)
        if not isinstance(token, str):
            raise CursorError("cursor token must be a string")
        try:
            padded = token + "=" * (-len(token) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
            if not decoded.startswith(_CURSOR_PREFIX):
                raise CursorError("cursor version mismatch")
            value = decoded[len(_CURSOR_PREFIX):]
            if not value.isdigit():
                raise CursorError("cursor sequence is invalid")
            return cls(int(value))
        except CursorError:
            raise
        except Exception as exc:
            raise CursorError("invalid cursor token") from exc
