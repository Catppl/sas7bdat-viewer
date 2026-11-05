from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QProcess, QThreadPool, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ...domain import DatasetHandle
from ...temp_manager import TempManager
from ...workers import Worker
from .manifest import P21SidecarManifest
from .protocol import (
    PROTOCOL_VERSION,
    FrameDecoder,
    ProtocolError,
    ProtocolMessage,
    encode_message,
)
from .snapshot import BuiltSnapshot, SnapshotBuilder, SnapshotRegistry


class P21HostController(QObject):
    """Own the optional process and expose only generic Viewer host capabilities."""

    status_changed = Signal(str)
    launch_failed = Signal(str)

    def __init__(
        self,
        manifest: P21SidecarManifest,
        temp_manager: TempManager,
        dataset_resolver: Callable[[str], DatasetHandle | None],
        workspace_context: Callable[[], dict[str, Any]],
        result_handler: Callable[[str, dict[str, Any]], dict[str, Any]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manifest = manifest
        self.dataset_resolver = dataset_resolver
        self.workspace_context = workspace_context
        self.result_handler = result_handler
        self.server = QLocalServer(self)
        self.process = QProcess(self)
        self.snapshot_builder = SnapshotBuilder(temp_manager)
        self.snapshots = SnapshotRegistry(temp_manager)
        self.pool = QThreadPool.globalInstance()
        self._snapshot_workers: set[Worker] = set()
        self.socket: QLocalSocket | None = None
        self.decoder = FrameDecoder()
        self.server_name = ""
        self.session_token = ""
        self._authenticated = False
        self._shutting_down = False
        self.server.newConnection.connect(self._accept_connection)
        self.process.started.connect(
            lambda: self.status_changed.emit("P21 Issue Explorer started.")
        )
        self.process.errorOccurred.connect(
            lambda _error: self.launch_failed.emit(self.process.errorString())
        )
        self.process.finished.connect(self._process_finished)

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def launch(self) -> None:
        if self.running:
            self.status_changed.emit("P21 Issue Explorer is already running.")
            return
        self._shutting_down = False
        self._reset_transport()
        # Unix local sockets have a short path limit after Qt prepends its temp
        # directory. Keep the random name compact while retaining 64 bits of entropy.
        self.server_name = f"p21-{uuid.uuid4().hex[:16]}"
        self.session_token = secrets.token_hex(32)
        if not self.server.listen(self.server_name):
            self.launch_failed.emit(self.server.errorString())
            self._reset_transport()
            return
        arguments = [
            *self.manifest.arguments,
            "--server-name",
            self.server_name,
            "--session-token",
            self.session_token,
            "--protocol-version",
            str(PROTOCOL_VERSION),
        ]
        self.process.start(str(self.manifest.executable), arguments)

    def shutdown(self) -> None:
        self._shutting_down = True
        self._cancel_snapshot_workers()
        if self.socket is not None and self._authenticated:
            self._send("shutdown", {})
            self.socket.flush()
        if self.running:
            self.process.terminate()
            if not self.process.waitForFinished(1_500):
                self.process.kill()
                self.process.waitForFinished(1_000)
        self.snapshots.clear()
        self._reset_transport()

    def _accept_connection(self) -> None:
        pending = self.server.nextPendingConnection()
        if pending is None:
            return
        if self.socket is not None:
            pending.abort()
            pending.deleteLater()
            return
        self.socket = pending
        self.socket.readyRead.connect(self._read_messages)
        self.socket.disconnected.connect(self._socket_disconnected)

    def _read_messages(self) -> None:
        if self.socket is None:
            return
        try:
            messages = self.decoder.feed(bytes(self.socket.readAll()))
            for message in messages:
                self._handle_message(message)
        except ProtocolError:
            self.socket.abort()

    def _handle_message(self, message: ProtocolMessage) -> None:
        if message.protocol_version != PROTOCOL_VERSION or not secrets.compare_digest(
            message.session_token, self.session_token
        ):
            if self.socket is not None:
                self.socket.abort()
            return
        if not self._authenticated:
            if message.message_type != "hello":
                if self.socket is not None:
                    self.socket.abort()
                return
            self._authenticated = True
            self._send("hello_ack", {"host": "SASDataViewer"}, message.request_id)
            return
        handlers = {
            "request_workspace_context": self._workspace_requested,
            "request_dataset_snapshot": self._snapshot_requested,
            "open_result_view": self._result_requested,
            "update_result_view": self._result_requested,
            "release_snapshot": self._release_snapshot,
            "shutdown_ack": lambda _message: None,
            "error": lambda _message: None,
        }
        handler = handlers.get(message.message_type)
        if handler is None:
            self._send_error(message.request_id, "Unsupported message type.")
            return
        try:
            handler(message)
        except Exception as error:  # noqa: BLE001 - IPC boundary must isolate Viewer
            self._send_error(message.request_id, str(error))

    def _workspace_requested(self, message: ProtocolMessage) -> None:
        self._send("workspace_context", self.workspace_context(), message.request_id)

    def _snapshot_requested(self, message: ProtocolMessage) -> None:
        domain = message.payload.get("domain")
        variables = message.payload.get("variables", [])
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("Snapshot request requires a domain.")
        if not isinstance(variables, list) or not all(
            isinstance(item, str) for item in variables
        ):
            raise ValueError("Snapshot variables must be an array of strings.")
        source = self.dataset_resolver(domain)
        if source is None:
            raise ValueError(
                f"No open current dataset is available for domain {domain}."
            )
        fingerprint = self.snapshot_builder.source_fingerprint(source)
        selected = self.snapshot_builder.select_variables(source, variables)
        selected_names = tuple(item.name for item in selected)
        self.snapshots.invalidate_changed_source(domain, fingerprint)
        cached = self.snapshots.acquire(domain, fingerprint, selected_names)
        if cached is not None:
            self._send_snapshot_ready(message.request_id, cached, cache_hit=True)
            return
        self.status_changed.emit(f"Preparing {domain.upper()} data snapshot…")
        worker = Worker(
            lambda owner: self.snapshot_builder.build(
                source,
                domain,
                variables,
                cancelled=lambda: owner.cancelled,
                progress=lambda count: owner.report(
                    f"Preparing {domain.upper()} snapshot… {count:,} rows"
                ),
            )
        )
        self._snapshot_workers.add(worker)
        worker.signals.progress.connect(self.status_changed)
        worker.signals.result.connect(
            lambda snapshot, request_id=message.request_id: self._snapshot_built(
                request_id, snapshot
            )
        )
        worker.signals.error.connect(
            lambda error, _details, request_id=message.request_id: self._send_error(
                request_id, f"Snapshot generation failed: {error}"
            )
        )

        def finished() -> None:
            self._snapshot_workers.discard(worker)

        worker.signals.finished.connect(finished)
        self.pool.start(worker)

    def _snapshot_built(self, request_id: str, snapshot: BuiltSnapshot) -> None:
        self.snapshots.add(snapshot)
        self._send_snapshot_ready(request_id, snapshot, cache_hit=False)

    def _send_snapshot_ready(
        self, request_id: str, snapshot: BuiltSnapshot, *, cache_hit: bool
    ) -> None:
        self._send(
            "snapshot_ready",
            {
                "snapshot_id": snapshot.snapshot_id,
                "path": str(snapshot.root),
                "source_fingerprint": snapshot.source_fingerprint,
                "variables": list(snapshot.variables),
                "cache_hit": cache_hit,
            },
            request_id,
        )
        self.status_changed.emit(
            f"P21 snapshot ready: {snapshot.domain} "
            f"({len(snapshot.variables)} variables, "
            f"{'cache hit' if cache_hit else 'created'})."
        )

    def _result_requested(self, message: ProtocolMessage) -> None:
        result = self.result_handler(message.message_type, message.payload)
        self._send("result_view_opened", result, message.request_id)

    def _release_snapshot(self, message: ProtocolMessage) -> None:
        snapshot_id = message.payload.get("snapshot_id")
        if isinstance(snapshot_id, str):
            self.snapshots.release(snapshot_id)

    def _send_error(self, request_id: str, message: str) -> None:
        self._send("error", {"message": message}, request_id)

    def _send(
        self,
        message_type: str,
        payload: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        if self.socket is None:
            return
        message = ProtocolMessage(
            PROTOCOL_VERSION,
            request_id or uuid.uuid4().hex,
            message_type,
            self.session_token,
            payload,
        )
        self.socket.write(encode_message(message))
        self.socket.flush()

    def _socket_disconnected(self) -> None:
        if self.socket is not None:
            self.socket.deleteLater()
        self.socket = None
        self._authenticated = False

    def _process_finished(self, *_args: object) -> None:
        self._cancel_snapshot_workers()
        self.snapshots.clear()
        self._reset_transport()
        if not self._shutting_down:
            self.status_changed.emit(
                "P21 Issue Explorer stopped. Use Tools to start it again."
            )

    def _cancel_snapshot_workers(self) -> None:
        for worker in tuple(self._snapshot_workers):
            worker.cancel()

    def _reset_transport(self) -> None:
        if self.socket is not None:
            socket = self.socket
            self.socket = None
            socket.abort()
            socket.deleteLater()
        self._authenticated = False
        self.decoder = FrameDecoder()
        if self.server.isListening():
            self.server.close()
        if self.server_name:
            QLocalServer.removeServer(self.server_name)
        self.server_name = ""
        self.session_token = ""
