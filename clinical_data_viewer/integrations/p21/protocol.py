from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
_HEADER = struct.Struct(">I")


class ProtocolError(ValueError):
    """A local IPC frame or message violates the P21 wire contract."""


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
        version = value.get("protocol_version")
        request_id = value.get("request_id")
        message_type = value.get("message_type")
        session_token = value.get("session_token")
        payload = value.get("payload")
        if not isinstance(version, int):
            raise ProtocolError("protocol_version must be an integer.")
        if not isinstance(request_id, str) or not request_id:
            raise ProtocolError("request_id must be a non-empty string.")
        if not isinstance(message_type, str) or not message_type:
            raise ProtocolError("message_type must be a non-empty string.")
        if not isinstance(session_token, str) or not session_token:
            raise ProtocolError("session_token must be a non-empty string.")
        if not isinstance(payload, dict):
            raise ProtocolError("payload must be an object.")
        return cls(version, request_id, message_type, session_token, payload)

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
    """Incrementally decode partial or coalesced length-prefixed JSON frames."""

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
            frame_end = _HEADER.size + length
            if len(self._buffer) < frame_end:
                break
            raw = bytes(self._buffer[_HEADER.size : frame_end])
            del self._buffer[:frame_end]
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ProtocolError(f"Invalid UTF-8 JSON payload: {error}") from error
            messages.append(ProtocolMessage.from_dict(value))
        return tuple(messages)
