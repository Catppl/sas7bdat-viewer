from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
_HEADER = struct.Struct(">I")


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProtocolMessage:
    protocol_version: int
    request_id: str
    message_type: str
    session_token: str
    payload: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Any) -> ProtocolMessage:
        if not isinstance(value, dict):
            raise ProtocolError("Message root must be an object.")
        fields = (
            value.get("protocol_version"),
            value.get("request_id"),
            value.get("message_type"),
            value.get("session_token"),
            value.get("payload"),
        )
        if not isinstance(fields[0], int):
            raise ProtocolError("protocol_version must be an integer.")
        if not isinstance(fields[1], str) or not fields[1]:
            raise ProtocolError("request_id must be a non-empty string.")
        if not isinstance(fields[2], str) or not fields[2]:
            raise ProtocolError("message_type must be a non-empty string.")
        if not isinstance(fields[3], str) or not fields[3]:
            raise ProtocolError("session_token must be a non-empty string.")
        if not isinstance(fields[4], dict):
            raise ProtocolError("payload must be an object.")
        return cls(*fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "message_type": self.message_type,
            "session_token": self.session_token,
            "payload": self.payload,
        }


def encode_message(message: ProtocolMessage) -> bytes:
    payload = json.dumps(
        message.to_dict(), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if not payload or len(payload) > MAX_PAYLOAD_BYTES:
        raise ProtocolError("Message payload length is outside the allowed range.")
    return _HEADER.pack(len(payload)) + payload


class FrameDecoder:
    def __init__(self, *, max_payload_bytes: int = MAX_PAYLOAD_BYTES) -> None:
        self._buffer = bytearray()
        self.max_payload_bytes = max_payload_bytes

    def feed(self, data: bytes | bytearray) -> tuple[ProtocolMessage, ...]:
        self._buffer.extend(data)
        messages: list[ProtocolMessage] = []
        while len(self._buffer) >= _HEADER.size:
            length = _HEADER.unpack(self._buffer[: _HEADER.size])[0]
            if length <= 0 or length > self.max_payload_bytes:
                self._buffer.clear()
                raise ProtocolError(f"Invalid frame payload length: {length}.")
            end = _HEADER.size + length
            if len(self._buffer) < end:
                break
            raw = bytes(self._buffer[_HEADER.size : end])
            del self._buffer[:end]
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ProtocolError(f"Invalid UTF-8 JSON payload: {error}") from error
            messages.append(ProtocolMessage.from_dict(value))
        return tuple(messages)
