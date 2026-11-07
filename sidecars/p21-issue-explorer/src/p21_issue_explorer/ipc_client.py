from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtNetwork import QLocalSocket

from .integration.protocol import (
    PROTOCOL_VERSION,
    FrameDecoder,
    ProtocolError,
    ProtocolMessage,
    encode_message,
)
from .integration.snapshot_reader import DatasetSnapshot, SnapshotReader
from .models import DomainExportRequest, IssueOpenRequest, RuleOpenRequest

_EXPORT_CHUNK_ROWS = 5_000


@dataclass(slots=True)
class _SnapshotRequest:
    domain: str
    variables: tuple[str, ...]
    event: threading.Event
    path: Path | None = None
    snapshot_id: str = ""
    error: str = ""


class ViewerSnapshotProvider(QObject):
    snapshot_requested = Signal(object)
    snapshot_released = Signal(str)

    def __init__(self, *, timeout_seconds: float = 60.0, parent=None) -> None:
        super().__init__(parent)
        self.timeout_seconds = timeout_seconds
        self.reader = SnapshotReader()

    def open_snapshot(
        self, domain: str, variables: tuple[str, ...] = ()
    ) -> DatasetSnapshot:
        request = _SnapshotRequest(domain, tuple(variables), threading.Event())
        self.snapshot_requested.emit(request)
        if not request.event.wait(self.timeout_seconds):
            raise TimeoutError(f"Timed out waiting for the {domain} dataset snapshot.")
        if request.error:
            raise RuntimeError(request.error)
        if request.path is None:
            raise RuntimeError("Viewer returned no snapshot path.")
        snapshot = self.reader.open(request.path)
        snapshot._on_close = lambda: self.snapshot_released.emit(request.snapshot_id)
        return snapshot


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


class P21IpcClient(QObject):
    connected_to_viewer = Signal()
    disconnected_from_viewer = Signal(str)

    def __init__(
        self,
        server_name: str,
        session_token: str,
        protocol_version: int = PROTOCOL_VERSION,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.server_name = server_name
        self.session_token = session_token
        self.protocol_version = protocol_version
        self.socket = QLocalSocket(self)
        self.decoder = FrameDecoder()
        self._authenticated = False
        self._pending_snapshots: dict[str, _SnapshotRequest] = {}
        self._snapshot_ids: set[str] = set()
        self.socket.connected.connect(self._send_hello)
        self.socket.readyRead.connect(self._read_messages)
        self.socket.disconnected.connect(
            lambda: self.disconnected_from_viewer.emit("Viewer connection closed.")
        )
        self.socket.errorOccurred.connect(
            lambda _error: self.disconnected_from_viewer.emit(self.socket.errorString())
        )

    def attach_provider(self, provider: ViewerSnapshotProvider) -> None:
        provider.snapshot_requested.connect(
            self._request_snapshot, Qt.ConnectionType.QueuedConnection
        )
        provider.snapshot_released.connect(
            self._release_snapshot, Qt.ConnectionType.QueuedConnection
        )

    def connect_to_viewer(self) -> None:
        self.socket.connectToServer(self.server_name)

    def send_issue_request(self, request: IssueOpenRequest) -> None:
        self._send("open_result_view", _json_value(request))

    def send_rule_request(self, request: RuleOpenRequest) -> None:
        self._send("update_result_view", _json_value(request))

    def send_domain_export_request(self, request: DomainExportRequest) -> None:
        request_id = uuid.uuid4().hex
        self._send(
            "begin_domain_export",
            {
                "domain": request.domain,
                "report_name": request.report_name,
                "source_fingerprint": request.source_fingerprint,
                "rules": [
                    {
                        "rule_id": rule.rule_id,
                        "p21_issue_count": rule.p21_issue_count,
                    }
                    for rule in request.rules
                ],
            },
            request_id=request_id,
        )
        for rule in request.rules:
            rows = rule.rows
            for offset in range(0, len(rows), _EXPORT_CHUNK_ROWS):
                self._send(
                    "append_domain_export_rows",
                    {
                        "rule_id": rule.rule_id,
                        "rows": [
                            _json_value(row)
                            for row in rows[offset : offset + _EXPORT_CHUNK_ROWS]
                        ],
                    },
                    request_id=request_id,
                )
        self._send("finish_domain_export", {}, request_id=request_id)

    @Slot(object)
    def _request_snapshot(self, request: _SnapshotRequest) -> None:
        if not self._authenticated:
            request.error = "The side-car has not completed the Viewer handshake."
            request.event.set()
            return
        request_id = uuid.uuid4().hex
        self._pending_snapshots[request_id] = request
        self._send(
            "request_dataset_snapshot",
            {"domain": request.domain, "variables": list(request.variables)},
            request_id=request_id,
        )

    def _send_hello(self) -> None:
        self._send("hello", {"client": "P21 Issue Explorer"})

    @Slot(str)
    def _release_snapshot(self, snapshot_id: str) -> None:
        if snapshot_id and snapshot_id in self._snapshot_ids:
            self._send("release_snapshot", {"snapshot_id": snapshot_id})
            self._snapshot_ids.discard(snapshot_id)

    def _send(
        self,
        message_type: str,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> str:
        identifier = request_id or uuid.uuid4().hex
        message = ProtocolMessage(
            self.protocol_version,
            identifier,
            message_type,
            self.session_token,
            payload,
        )
        self.socket.write(encode_message(message))
        self.socket.flush()
        return identifier

    def _read_messages(self) -> None:
        try:
            messages = self.decoder.feed(bytes(self.socket.readAll()))
            for message in messages:
                self._handle_message(message)
        except ProtocolError as error:
            self.disconnected_from_viewer.emit(str(error))
            self.socket.abort()

    def _handle_message(self, message: ProtocolMessage) -> None:
        if (
            message.protocol_version != self.protocol_version
            or message.session_token != self.session_token
        ):
            self.socket.abort()
            return
        if message.message_type == "hello_ack":
            self._authenticated = True
            self.connected_to_viewer.emit()
            return
        if message.message_type == "snapshot_ready":
            request = self._pending_snapshots.pop(message.request_id, None)
            if request is None:
                return
            path = message.payload.get("path")
            snapshot_id = message.payload.get("snapshot_id")
            if isinstance(path, str) and isinstance(snapshot_id, str):
                request.path = Path(path)
                request.snapshot_id = snapshot_id
                self._snapshot_ids.add(snapshot_id)
            else:
                request.error = "Viewer returned an invalid snapshot response."
            request.event.set()
            return
        if message.message_type == "error":
            request = self._pending_snapshots.pop(message.request_id, None)
            if request is not None:
                request.error = str(
                    message.payload.get("message", "Viewer request failed.")
                )
                request.event.set()
            return
        if message.message_type == "shutdown":
            for snapshot_id in tuple(self._snapshot_ids):
                self._send("release_snapshot", {"snapshot_id": snapshot_id})
            self._send("shutdown_ack", {})
            self.socket.disconnectFromServer()
