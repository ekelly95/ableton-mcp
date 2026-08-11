"""Socket client for the control surface running inside Live.

Persistent connection with lazy connect and one reconnect-and-resend attempt
(covers Live being restarted between tool calls). A lock serializes sends:
the control surface serves one client serially, so concurrent MCP tool calls
must queue here rather than interleave frames.
"""

import json
import logging
import socket
import struct
import threading
import uuid
from typing import Any, Dict, Optional

HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 16 * 1024 * 1024

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877
DEFAULT_TIMEOUT = 90.0  # above the control surface's longest command timeout
CONNECT_TIMEOUT = 5.0

logger = logging.getLogger("ableton-mcp.client")

NOT_RUNNING_HINT = (
    "Is Ableton Live running with the AbletonMCP control surface enabled? "
    "(Preferences > Link, Tempo & MIDI > Control Surface > AbletonMCP)"
)


class AbletonConnectionError(Exception):
    """Live is unreachable (not running, script disabled, or port blocked)."""


class CommandError(Exception):
    """The control surface executed the request and reported a failure."""

    def __init__(self, message: str, error_type: str = "unknown", param: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.param = param

    def __str__(self) -> str:
        parts = [self.message]
        if self.error_type != "unknown":
            parts.append(f"[{self.error_type}]")
        if self.param:
            parts.append(f"(param: {self.param})")
        return " ".join(parts)


class AbletonClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None
        self._lock = threading.Lock()

    # -- connection management -------------------------------------------------

    def _connect(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        except OSError as e:
            raise AbletonConnectionError(
                f"Cannot connect to {self.host}:{self.port}. {NOT_RUNNING_HINT} ({e})"
            ) from e
        sock.settimeout(self.timeout)
        self._socket = sock
        logger.info(f"Connected to control surface at {self.host}:{self.port}")

    def _disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def close(self) -> None:
        with self._lock:
            self._disconnect()

    # -- request/response ------------------------------------------------------

    def send(self, command: str, **params: Any) -> Any:
        """Send a command and return its result. Thread-safe; serialized."""
        request = {"type": command, "params": params, "id": str(uuid.uuid4())}
        with self._lock:
            if self._socket is None:
                self._connect()
            try:
                response = self._round_trip(request)
            except OSError:
                # One reconnect-and-resend: covers Live restarts and half-open
                # sockets left by a Live crash. Resending is safe on OSError
                # because the request never completed on the serial server.
                # Timeouts deliberately do NOT land here (they raise
                # AbletonConnectionError): the command may still be running
                # inside Live and a resend would queue it twice.
                logger.warning(f"Connection lost during '{command}', reconnecting once...")
                self._disconnect()
                self._connect()
                response = self._round_trip(request)

        if response.get("status") == "success":
            return response.get("result")
        raise CommandError(
            message=response.get("error", "Unknown error"),
            error_type=response.get("error_type", "unknown"),
            param=response.get("param"),
        )

    def ping(self) -> Optional[Dict[str, Any]]:
        try:
            result = self.send("ping")
            return result if isinstance(result, dict) and result.get("pong") else None
        except (AbletonConnectionError, CommandError):
            return None

    def _round_trip(self, request: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_MESSAGE_SIZE:
            raise ValueError(f"Request too large: {len(body)} bytes")
        try:
            self._socket.sendall(struct.pack(">I", len(body)) + body)
            header = self._recv_exact(HEADER_SIZE)
            length = struct.unpack(">I", header)[0]
            if length > MAX_MESSAGE_SIZE:
                raise ValueError(f"Response too large: {length} bytes")
            payload = self._recv_exact(length) if length else b"{}"
        except socket.timeout as e:
            # Do NOT resend after a timeout: the command may still be running
            # inside Live, and a resend would queue it twice.
            self._disconnect()
            raise AbletonConnectionError(
                f"No response within {self.timeout}s — Live may be busy (modal dialog, "
                f"loading) or the command is very slow. Connection reset."
            ) from e
        return json.loads(payload.decode("utf-8"))

    def _recv_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self._socket.recv(min(8192, size - len(data)))
            if not chunk:
                raise OSError("Connection closed by control surface")
            data.extend(chunk)
        return bytes(data)
